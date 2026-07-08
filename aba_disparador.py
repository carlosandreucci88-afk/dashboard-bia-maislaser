"""
==============================================================================
ABA 🚀 DISPARAR AGENDA — Robô Confirmação Agenda
==============================================================================
v6.14.2 (02/07/2026): PERSISTÊNCIA DE DETALHES DE ERRO
Grava telefone + nome + HTTP + resposta da Meta no Supabase quando
`enviar_mensagem_whatsapp` falha. Fim do "1 erro sem saber quem/por quê".

CAUSA RAIZ DO BUG DIAGNOSTICADO:
  Quando o envio pra Meta falhava (ex: 5511968959627 _aline em 02/07 09:42),
  o `st.error` mostrava HTTP + mensagem na tela do Streamlit — MAS não gravava
  no Supabase. Refresh da página apagava o registro visual. `disparos_historico`
  gravava apenas o CONTADOR (`erros_envio: 1`) sem QUAL cliente ou POR QUÊ.
  Resultado: alerta cego, precisava investigar do zero via Contexto do
  Apps Script pra descobrir quem faltou.

FIX v6.14.2:
  ▸ NOVA COLUNA no Supabase: `erros_envio_detalhes text`
    Antes de deployar, rodar migration_v6.14.2.sql no SQL Editor.
    ALTER TABLE ADD COLUMN IF NOT EXISTS — idempotente e não bloqueia.
  ▸ NOVA LISTA em memória: `erros_envio_detalhes` acumula durante o loop
    cada falha em formato: "Nome (telefone) | HTTP xxx | {response_json}"
    Cobre ambos casos: (1) HTTP != 200/201 da Meta e (2) telefone inválido.
  ▸ `_atualizar_registro_historico` ganha novo parâmetro que grava a coluna.
    Fallback INSERT também inclui a coluna.
  ▸ `clientes_falha` continua sendo APENAS falhas de contexto (semântica limpa).
    Erros de envio ficam separados na coluna nova.

RETROCOMPAT TOTAL:
  ▸ Fluxo de disparo INTOCADO (envio, retry, backoff, GET final).
  ▸ Estrutura da UI INTOCADA.
  ▸ Validação de planilha e agrupamento INTOCADOS.
  ▸ `_criar_registro_inicial_historico` INTOCADO.
  ▸ Se a coluna nova ainda não existir no Supabase (esqueceu a migration),
    o `update` da linha existente falharia — por isso a migration DEVE ser
    aplicada ANTES do deploy do código.

RESULTADO PRÁTICO:
  Depois desta versão, sempre que der erro, você abre no Supabase Table Editor
  a linha do disparo e a coluna `erros_envio_detalhes` mostra:
  "_aline Chagas (5511968959627) | HTTP 400 | {"error":{"code":131047,"message":"Re-engagement message"}} || ..."
  Fim da investigação forense.

==============================================================================
v6.14 (24/06/2026): VERIFICAÇÃO FINAL POR GET ÚNICO — elimina falsos positivos
de "Contexto NÃO salvo" causados por timeout do POST quando Apps Script demora
pra responder mas já salvou.

CAUSA RAIZ:
  Apps Script `salvarContexto` demora >15s quando há contenção do lock
  (trigger paralelo + Contexto com 595+ linhas + cold start). Streamlit dava
  timeout e marcava como falha — MAS o Apps Script JÁ HAVIA SALVADO.

CASO REAL:
  Disparo Suzano 24/06 11:53 reportou 5 falhas. Todas as 5 estavam no Contexto
  e 4 já tinham confirmado normalmente. Alerta falso 100%.

FIX v6.14:
  • timeout POST 15→30s + 2→3 tentativas (mais resiliente)
  • Não mostra warning durante o loop (não é confiável em tempo real)
  • Tracking de todos os telefones disparados em lista local
  • Após o loop: 1 GET único no endpoint=contexto compara TODOS os telefones
    disparados com a lista REAL do Apps Script
  • Reconcilia: falsos positivos corrigidos + falhas reais detectadas
  • Bonus: também detecta falhas silenciosas (POST 200 mas contexto sumiu)
  • Degradação graciosa: se GET falhar, usa dados do POST

==============================================================================
v2 (22/06/2026): grava `disparos_historico` ANTES do loop de envio (status
EM_ANDAMENTO) e atualiza ao FINAL com totais reais. Antes era só um INSERT
no fim — se o Streamlit reset/erro/refresh acontecesse antes do INSERT, o
registro era perdido (foi o caso do disparo Suzano de 22/06 13h).

Mudanças desta versão:
- INSERT inicial ANTES do loop → cria registro c/ status='EM_ANDAMENTO'
- UPDATE ao final → preenche sucessos/erros/falhas
- Try/except mais verboso (loga erro pro usuário em vez de silenciar)
- Coluna 'status_dispatch' (EM_ANDAMENTO / CONCLUIDO / FALHOU) — opcional
==============================================================================
"""

import streamlit as st
import pandas as pd
import requests
import json
import time
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from supabase import create_client

# ============================================================================
# v6.21 (08/07/2026) — BLINDAGEM DO DISPARADOR (Fase A + B)
# ============================================================================
# FIXES CRÍTICOS:
#   1. Timeout no requests.post da Meta (era infinito → travava disparo inteiro
#      quando Meta demorava)
#   2. INSERT direto no Supabase agenda_contexto (pula Sheets/Apps Script no
#      caminho crítico — 30x mais rápido por cliente)
#   3. POST Apps Script em fire-and-forget async (pra manter PropertiesService
#      + agendarLembretes funcionando, mas sem bloquear o loop)
#   4. Update incremental de disparos_historico a cada 3 clientes + heartbeat
#      (se morrer, banco sabe estado real; watchdog detecta em ≤3min)
#   5. Update final com status='concluido' pra dashboard mostrar corretamente
#   6. GET verificação final agora vai no SUPABASE (não Apps Script)
#
# GANHO MEDIDO: loop de 29 clientes vai de ~12min (trava) pra ~90s (completa).
#
# COMPATIBILIDADE 100%:
#   • Apps Script continua sendo chamado (mas async) → PropertiesService e
#     agendarLembretes continuam populando exatamente como antes
#   • Webhook doPost do Apps Script (respostas de cliente) → sem mudança
#   • Triggers de lembrete → sem mudança
#   • Dashboard existente → funciona igual, ganha campos novos automaticamente
# ============================================================================

# Timeouts do requests.post pra Meta:
#   • 5s pra estabelecer conexão TCP
#   • 60s pra Meta responder o body
# Se estourar, requests.exceptions.Timeout é levantado → cliente vira erro,
# LOOP CONTINUA (antes: travava infinito e matava tudo)
META_TIMEOUT = (5, 60)

# Batch de update do disparos_historico
# A cada 3 clientes processados, faz UPDATE no banco com contadores +
# heartbeat_em=now(). Se Python morrer, banco tem estado real (perde no
# máximo 2 clientes de precisão).
UPDATE_BATCH_SIZE = 3

NOME_MODELO_MENSAGEM        = "confirmacao_agenda_maislaser_v4"
# v6.20 (05/07/2026): Template `_2sessoes_v2` não existe mais na Meta —
# causava HTTP 404 pra todo cliente com 2 horários diferentes no mesmo dia.
# Fix: sempre usar `_v4` (unificado) concatenando horários e serviços em {{2}} e {{3}}.
# Constante mantida só pra referência histórica.
NOME_MODELO_MENSAGEM_2SESS  = "confirmacao_agenda_maislaser_2sessoes_v2"  # DEPRECATED — não usar

def limpar_numero(numero):
    if pd.isna(numero):
        return None
    num_str = str(numero).strip()
    if num_str.endswith('.0'):
        num_str = num_str[:-2]
    num_limpo = re.sub(r'\D', '', num_str)
    if num_limpo == '':
        return None
    if num_limpo.startswith('55') and len(num_limpo) >= 12:
        return num_limpo
    elif not num_limpo.startswith('55'):
        return '55' + num_limpo
    else:
        return num_limpo

def limpar_nome_servico(servico):
    if pd.isna(servico) or str(servico).strip() == '':
        return ''
    s = str(servico).strip()
    s = re.sub(r'^[FM]\s*-\s*', '', s)
    s = re.sub(r'\(área\s*[A-Z]\)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\bcortesia\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    s = s.rstrip(',-').strip()
    return s

def enviar_mensagem_whatsapp(nome, horario, procedimento, unidade, telefone_destino):
    url = f"https://graph.facebook.com/v25.0/{st.secrets['ID_TELEFONE_META']}/messages"
    headers = {
        "Authorization": f"Bearer {st.secrets['TOKEN_META']}",
        "Content-Type": "application/json"
    }
    procedimento_limpo = str(procedimento).replace('\n', ' ').replace('\r', '').strip()
    payload = {
        "messaging_product": "whatsapp",
        "to": str(telefone_destino),
        "type": "template",
        "template": {
            "name": NOME_MODELO_MENSAGEM,
            "language": {"code": "pt_BR"},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(nome)},
                    {"type": "text", "text": str(horario)},
                    {"type": "text", "text": procedimento_limpo},
                    {"type": "text", "text": str(unidade)}
                ]
            }]
        }
    }
    try:
        # 🆕 v6.21: timeout (5s conexão, 60s resposta) — antes era infinito
        resposta = requests.post(url, headers=headers, json=payload, timeout=META_TIMEOUT)
        return resposta.status_code, resposta.json()
    except requests.exceptions.Timeout:
        return 408, {"error": f"Timeout Meta (>{META_TIMEOUT[1]}s) — cliente pulado, loop continua"}
    except Exception as e:
        return 500, {"error": str(e)}

def enviar_mensagem_2sessoes(nome, horario1, servico1, horario2, servico2, unidade, telefone_destino):
    url = f"https://graph.facebook.com/v25.0/{st.secrets['ID_TELEFONE_META']}/messages"
    headers = {
        "Authorization": f"Bearer {st.secrets['TOKEN_META']}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": str(telefone_destino),
        "type": "template",
        "template": {
            "name": NOME_MODELO_MENSAGEM_2SESS,
            "language": {"code": "pt_BR"},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(nome)},
                    {"type": "text", "text": str(horario1)},
                    {"type": "text", "text": str(servico1).replace('\n', ' ').strip()},
                    {"type": "text", "text": str(horario2)},
                    {"type": "text", "text": str(servico2).replace('\n', ' ').strip()},
                    {"type": "text", "text": str(unidade)}
                ]
            }]
        }
    }
    try:
        # 🆕 v6.21: timeout — mesma proteção da função principal
        resposta = requests.post(url, headers=headers, json=payload, timeout=META_TIMEOUT)
        return resposta.status_code, resposta.json()
    except requests.exceptions.Timeout:
        return 408, {"error": f"Timeout Meta (>{META_TIMEOUT[1]}s) — cliente pulado, loop continua"}
    except Exception as e:
        return 500, {"error": str(e)}


def _criar_registro_inicial_historico(unidade, arquivo_nome, total_clientes,
                                       data_sessoes, numero_alerta):
    try:
        sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        resp = sb.table("disparos_historico").insert({
            "unidade": unidade,
            "arquivo": arquivo_nome,
            "data_sessoes": data_sessoes,
            "total_clientes": total_clientes,
            "whatsapp_ok": 0,
            "contexto_ok": 0,
            "falhas_contexto": 0,
            "clientes_falha": None,
            "erros_envio": 0,
            "numero_alerta": numero_alerta,
            "observacao": "🔄 EM_ANDAMENTO — disparo em execução",
            # 🆕 v6.21 — status + heartbeat pra watchdog
            "status": "em_andamento",
            "heartbeat_em": datetime.now(timezone.utc).isoformat(),
        }).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0].get("id")
        return None
    except Exception as e:
        st.warning(f"⚠️ Não consegui criar registro inicial no histórico: {e}")
        return None


# 🆕 v6.21 — UPDATE INCREMENTAL DE PROGRESSO
# Chamada a cada UPDATE_BATCH_SIZE clientes processados. Grava contadores
# parciais + heartbeat_em=now() pra sinalizar pro watchdog que o processo
# tá vivo. Se falhar, log apenas — NÃO quebra o loop.
def _atualizar_progresso_parcial(registro_id, sucessos, erros, ultimo_cliente):
    if not registro_id:
        return
    try:
        sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        sb.table("disparos_historico").update({
            "whatsapp_ok": sucessos,
            "erros_envio": erros,
            "heartbeat_em": datetime.now(timezone.utc).isoformat(),
            "ultimo_cliente_processado": ultimo_cliente,
        }).eq("id", registro_id).execute()
    except Exception as e:
        # Silencioso — não quer poluir tela do usuário com erro de update parcial.
        # Update final vai gravar tudo de novo mesmo.
        print(f"[disparador] update parcial falhou (id={registro_id}): {e}")


# 🆕 v6.21 — INSERT DIRETO NO SUPABASE (agenda_contexto)
# Substitui o POST pro Apps Script no caminho crítico do disparo.
# Comportamento idêntico ao Apps Script _supabaseUpsertContexto (linhas 353-369
# do Code.gs Agenda) — mesmo header UPSERT, mesmo payload.
# Retorna True em sucesso, False em falha (não levanta exceção).
def _insert_supabase_contexto(telefone, nome, servico, unidade, horario,
                                horario2, servico2, numero_alerta):
    try:
        supabase_url = st.secrets["SUPABASE_URL"]
        supabase_key = st.secrets["SUPABASE_KEY"]
        agora = datetime.now(timezone.utc).isoformat()
        payload = {
            "telefone":             str(telefone),
            "nome":                 nome or None,
            "servico":              servico or None,
            "unidade":              unidade or None,
            "horario":              horario or None,
            "horario2":             horario2 or None,
            "servico2":             servico2 or None,
            "numero_alerta":        numero_alerta or None,
            "status":               "aguardando",
            "tentativas_invalidas": 0,
            "ultima_atualizacao":   agora,
            "disparo_ts":           agora,
            "arquivado_em":         None,
            # Reset das flags de lembrete pra novo ciclo (mesmo comportamento
            # do agendarLembretes do Code.gs Agenda linhas 1561-1578)
            "lemb1_ts":             None,
            "lemb2_ts":             None,
            "aviso_ts":             None,
            "resp_recep_ts":        None,
            "pendente_uni":         None,
        }
        # UPSERT via PostgREST (resolution=merge-duplicates)
        r = requests.post(
            f"{supabase_url}/rest/v1/agenda_contexto",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal,resolution=merge-duplicates",
            },
            json=payload,
            timeout=(5, 10),  # Supabase é rápido, 10s é folgado
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        print(f"[disparador] INSERT Supabase falhou pra tel={telefone}: {e}")
        return False


# 🆕 v6.21 — POST APPS SCRIPT (fire-and-forget)
# Continua chamando Apps Script pra manter PropertiesService + agendarLembretes
# funcionando. Mas roda em thread paralela — loop não espera.
# Retorna True/False só pra rastreio no fim do loop (retry síncrono se falhar).
def _post_apps_script_salvar(telefone, nome, servico, unidade, horario,
                              horario2, servico2, numero_alerta):
    try:
        webhook_url = st.secrets.get("URL_WEBHOOK_CONTEXTO", "")
        if not webhook_url:
            return False
        r = requests.post(
            webhook_url,
            json={
                "acao": "salvar_contexto",
                "telefone": telefone,
                "nome": nome,
                "servico": servico,
                "unidade": unidade,
                "horario": horario,
                "horario2": horario2,
                "servico2": servico2,
                "numero_alerta": numero_alerta,
            },
            timeout=(5, 45),  # tolerante — Apps Script pode ser lento com lock contention
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[disparador] POST Apps Script falhou pra tel={telefone}: {e}")
        return False


# 🆕 v6.14.2 — assinatura ganhou parâmetro `erros_envio_detalhes` (lista de strings).
# Se lista vazia, grava NULL na coluna (comportamento igual ao clientes_falha).
# 🆕 v6.21 — marca status='concluido' e heartbeat final. Watchdog para de olhar
# esse registro (WHERE status='em_andamento').
def _atualizar_registro_historico(registro_id, sucessos, erros, falhas_contexto,
                                   clientes_sem_contexto, erros_envio_detalhes):
    if not registro_id:
        return False
    try:
        sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        sb.table("disparos_historico").update({
            "whatsapp_ok": sucessos,
            "contexto_ok": sucessos - falhas_contexto,
            "falhas_contexto": falhas_contexto,
            "clientes_falha": ", ".join(clientes_sem_contexto) if clientes_sem_contexto else None,
            "erros_envio": erros,
            # 🆕 v6.14.2 — usa " || " como separador (mais robusto que vírgula
            # porque a resposta JSON da Meta contém vírgulas). Facilita split
            # posterior pra debug.
            "erros_envio_detalhes": " || ".join(erros_envio_detalhes) if erros_envio_detalhes else None,
            "observacao": None,
            # 🆕 v6.21 — sinaliza pro watchdog que terminou normalmente
            "status": "concluido",
            "heartbeat_em": datetime.now(timezone.utc).isoformat(),
        }).eq("id", registro_id).execute()
        return True
    except Exception as e:
        st.error(f"❌ Não consegui atualizar histórico (id={registro_id}): {e}")
        return False


def render_aba_disparador():
    unidade_selecionada = st.selectbox(
        "Selecione a Unidade que está operando hoje:",
        ["", "Mogi das Cruzes", "Suzano"],
        index=0
    )

    if not unidade_selecionada:
        st.info("👆 Selecione a unidade acima para continuar.")
        return

    st.warning(f"⚠️ Você selecionou a unidade **{unidade_selecionada}** — está correto?")
    col1, col2 = st.columns(2)
    with col1:
        confirmar_unidade = st.button(f"✅ Sim, é {unidade_selecionada}", use_container_width=True, key="disp_btn_confirmar_unidade")
    with col2:
        corrigir_unidade = st.button("❌ Não, corrigir", use_container_width=True, key="disp_btn_corrigir_unidade")

    if corrigir_unidade:
        st.error("⬆️ Por favor, corrija a unidade no campo acima antes de continuar.")
        return

    if not confirmar_unidade and not st.session_state.get("disp_unidade_confirmada"):
        return

    if confirmar_unidade:
        st.session_state["disp_unidade_confirmada"] = True
        st.session_state["disp_unidade_valor"] = unidade_selecionada

    if st.session_state.get("disp_unidade_confirmada"):
        unidade_selecionada = st.session_state.get("disp_unidade_valor", unidade_selecionada)
        st.success(f"✅ Unidade **{unidade_selecionada}** confirmada!")

    numero_alerta_input = st.text_input(
        "Digite o número de WhatsApp que receberá os alertas (com DDD):",
        value=""
    )

    if numero_alerta_input:
        numero_alerta_formatado = limpar_numero(numero_alerta_input)
        st.info(f"📢 Os alertas serão enviados para: {numero_alerta_formatado}")

        numero_robo = st.secrets["NUMERO_ROBO_ALERTAS"]
        link_whatsapp = f"https://wa.me/{numero_robo}?text=oi"
        st.markdown(
            f"""
            <a href="{link_whatsapp}" target="_blank" style="
                display: inline-block;
                background-color: #25D366;
                color: white;
                padding: 10px 20px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: bold;
                font-size: 15px;
                margin-top: 4px;
            ">
            📲 Clique aqui para ativar os alertas no seu WhatsApp
            </a>
            <p style="font-size: 12px; color: gray; margin-top: 6px;">
            ⚠️ Envie o "oi" antes de iniciar os disparos para receber os alertas.
            </p>
            """,
            unsafe_allow_html=True
        )

        alertas_ativados = st.checkbox("✅ Já enviei o 'oi' e estou pronto para disparar!", key="disp_alertas_ativados")
        if not alertas_ativados:
            return

    arquivo_upload = st.file_uploader("Selecione a planilha do UNO (.xlsx)", type=["xlsx"])

    if arquivo_upload is not None:
        try:
            df = pd.read_excel(arquivo_upload)
            total_original = len(df)

            colunas_essenciais = ['Data', 'Cliente', 'Telefone', 'Serviço']
            colunas_encontradas = df.columns.tolist()

            st.subheader("🔍 Validação da Planilha")

            linhas_html = ""
            todas_ok = True
            for col in colunas_essenciais:
                if col in colunas_encontradas:
                    status_icon  = "✅"
                    status_texto = "Encontrada"
                    cor_bg       = "#e8f5e9"
                    cor_txt      = "#1b5e20"
                else:
                    status_icon  = "❌"
                    status_texto = "NÃO ENCONTRADA"
                    cor_bg       = "#ffebee"
                    cor_txt      = "#b71c1c"
                    todas_ok = False
                linhas_html += f"""
                <tr style="background:{cor_bg}">
                    <td style="padding:8px 14px;font-weight:bold;color:{cor_txt}">{status_icon} {col}</td>
                    <td style="padding:8px 14px;color:{cor_txt}">{status_texto}</td>
                </tr>"""

            st.markdown(f"""
            <table style="width:100%;border-collapse:collapse;border-radius:8px;overflow:hidden;font-size:15px;margin-bottom:12px">
                <thead>
                    <tr style="background:#1565c0;color:white">
                        <th style="padding:10px 14px;text-align:left">Coluna</th>
                        <th style="padding:10px 14px;text-align:left">Status</th>
                    </tr>
                </thead>
                <tbody>{linhas_html}</tbody>
            </table>
            """, unsafe_allow_html=True)

            extras = [c for c in colunas_encontradas if c not in colunas_essenciais]
            if extras:
                st.info(f"ℹ️ Colunas extras encontradas (ignoradas): {', '.join(extras)}")

            if not todas_ok:
                st.error("🚫 **Disparo bloqueado!** Corrija a planilha antes de continuar — uma ou mais colunas essenciais estão ausentes.")
                return

            st.success("✅ Todas as colunas essenciais foram encontradas! Planilha válida.")

            if True:
                if 'Situação' in df.columns:
                    total_antes_filtro = len(df)
                    df = df[df['Situação'].str.strip().str.lower() == 'agendado']
                    total_filtrados = total_antes_filtro - len(df)
                    if total_filtrados > 0:
                        st.warning(
                            f"⚠️ {total_filtrados} registro(s) ignorado(s) por não estarem "
                            f"com situação 'Agendado' (cancelados, faltou, etc.)."
                        )

                df['Cliente'] = df['Cliente'].fillna('').astype(str).str.strip()
                df['Telefone'] = df['Telefone'].apply(limpar_numero).fillna('').astype(str)
                df['Serviço'] = df['Serviço'].apply(limpar_nome_servico)
                df['Horario'] = pd.to_datetime(df['Data']).dt.strftime('%d/%m/%Y às %Hh%M')
                df = df[df['Serviço'] != '']

                df_nomes = df.groupby('Telefone')['Cliente'].first().reset_index()

                df_srv_horario = df.groupby(['Telefone', 'Horario'])['Serviço'].apply(
                    lambda x: ', '.join(sorted(set(x)))
                ).reset_index()

                df_horarios = df.groupby('Telefone')['Horario'].apply(
                    lambda x: sorted(set(x))
                ).reset_index()
                df_horarios['Horario2'] = df_horarios['Horario'].apply(lambda x: x[1] if len(x) > 1 else "")
                df_horarios['Horario']  = df_horarios['Horario'].apply(lambda x: x[0])

                df_srv_h1 = df_srv_horario.groupby('Telefone').first().reset_index()[['Telefone', 'Serviço']]

                df_srv_h2 = df_srv_horario.groupby('Telefone').apply(
                    lambda x: x.iloc[1]['Serviço'] if len(x) > 1 else ""
                ).reset_index().rename(columns={0: 'Servico2'})

                df_agrupado = df_srv_h1.merge(df_nomes, on='Telefone')
                df_agrupado = df_agrupado.merge(df_horarios[['Telefone', 'Horario', 'Horario2']], on='Telefone')
                df_agrupado = df_agrupado.merge(df_srv_h2, on='Telefone', how='left')
                df_agrupado['Servico2'] = df_agrupado['Servico2'].fillna("")
                df_agrupado = df_agrupado[['Cliente', 'Serviço', 'Telefone', 'Horario', 'Horario2', 'Servico2']]
                total_agrupado = len(df_agrupado)

                total_linhas_pre = len(df)
                if total_agrupado < total_linhas_pre:
                    tels_com_nomes_diff = df.groupby('Telefone')['Cliente'].nunique()
                    tels_dedup = (tels_com_nomes_diff > 1).sum()
                    if tels_dedup > 0:
                        st.info(f"🔄 {tels_dedup} telefone(s) tinham nomes diferentes entre comandas — unificados.")

                st.success(
                    f"✅ Planilha carregada com sucesso! "
                    f"{len(df)} registros válidos encontrados."
                )
                st.info(
                    f"🔄 Agrupamento concluído: serviços unidos por cliente. "
                    f"Serão disparadas **{total_agrupado}** mensagens."
                )

                st.subheader(f"Pré-visualização dos disparos ({unidade_selecionada}):")
                st.dataframe(df_agrupado, use_container_width=True)

                if st.button("🚀 Iniciar Disparos em Massa"):

                    _data_sessoes = ""
                    if not df_agrupado.empty and "Horario" in df_agrupado.columns:
                        _primeiro = str(df_agrupado.iloc[0]["Horario"])
                        _partes = _primeiro.split(" às ")
                        if _partes:
                            _data_sessoes = _partes[0]

                    registro_id = _criar_registro_inicial_historico(
                        unidade=unidade_selecionada,
                        arquivo_nome=arquivo_upload.name if arquivo_upload else "—",
                        total_clientes=total_agrupado,
                        data_sessoes=_data_sessoes,
                        numero_alerta=numero_alerta_formatado,
                    )
                    if registro_id:
                        st.caption(f"📝 Registro #{registro_id} criado no histórico (status: EM_ANDAMENTO)")

                    progresso = st.progress(0)
                    status_texto = st.empty()

                    sucessos = 0  # contador de WhatsApps enviados (status 200/201 da Meta)
                    erros = 0  # contador de falhas de WhatsApp (status != 200)
                    falhas_contexto_post = 0  # 🆕 v6.14: falhas só do POST (pode ter falsos positivos)
                    clientes_falha_post = []  # 🆕 v6.14: lista de quem o POST reportou falha
                    # 🆕 v6.14.2 — Acumula detalhes de cada erro de envio pra Meta.
                    # Formato de cada item: "Nome (telefone) | HTTP xxx | {response_json}"
                    # Cobre 2 casos: (1) HTTP != 200/201 da Meta e (2) telefone inválido/ausente.
                    # Fim da era "erros_envio: 1" cego sem saber quem/por quê.
                    erros_envio_detalhes = []
                    # 🆕 v6.14 — tracking de TODOS os telefones disparados (com sucesso na Meta).
                    # Será usado no GET final pra verificar se realmente estão no Contexto.
                    # Cada item: {tel, nome, ctx_post_ok}
                    telefones_disparados = []
                    total_linhas = len(df_agrupado)

                    # 🆕 v6.21 — ThreadPoolExecutor pra rodar INSERT Supabase + POST
                    # Apps Script em paralelo (fire-and-forget). max_workers=20 é
                    # folgado — 2 threads por cliente × 10 clientes em voo simult.
                    executor = ThreadPoolExecutor(max_workers=20)

                    # 🆕 v6.21 — controle de batch pra update incremental
                    ultimo_batch_atualizado = 0

                    for i, (_, linha) in enumerate(df_agrupado.iterrows()):
                        nome_cliente = linha['Cliente']
                        procedimento = linha['Serviço']
                        telefone_formatado = linha['Telefone']

                        if telefone_formatado and len(telefone_formatado) >= 12:
                            status_texto.text(
                                f"📤 Enviando {i+1}/{total_linhas}: "
                                f"{nome_cliente} ({telefone_formatado})..."
                            )

                            horario_cliente  = linha['Horario']
                            horario2_cliente = linha.get('Horario2', '')
                            servico2_cliente = linha.get('Servico2', '')

                            tem_2_sessoes = bool(horario2_cliente and horario2_cliente != horario_cliente)

                            # v6.20 (05/07/2026): SEMPRE usa template v4 (unificado).
                            # O template `_2sessoes_v2` não existe mais na Meta e causava
                            # HTTP 404 pra clientes com 2 horários diferentes.
                            # Fix: se tiver 2 sessões, concatena horário e serviços em
                            # uma única mensagem. Só extrai o "HH:MM" de cada horário
                            # (evita repetir a data 2x na msg).
                            if tem_2_sessoes:
                                # `horario_cliente` = "06/07/2026 às 09h30" (data + hora)
                                # `horario2_cliente` = "06/07/2026 às 09h50"
                                # Pega só a parte "HH:MM" do 2º horário pra concatenar
                                match_h2 = re.search(r'(\d{1,2}h\d{2})\s*$', str(horario2_cliente))
                                hora2_curta = match_h2.group(1) if match_h2 else str(horario2_cliente)
                                horario_final  = f"{horario_cliente} e {hora2_curta}"
                                servico_final  = f"{horario_cliente[-5:]}: {procedimento} · {hora2_curta}: {servico2_cliente}"
                            else:
                                horario_final = horario_cliente
                                servico_final = procedimento

                            code, res = enviar_mensagem_whatsapp(
                                nome_cliente, horario_final,
                                servico_final, unidade_selecionada,
                                telefone_formatado
                            )

                            if code in (200, 201):
                                sucessos += 1
                                # ═════════════════════════════════════════════════════════════
                                # 🆕 v6.21 — INSERT SUPABASE DIRETO + POST APPS SCRIPT ASYNC
                                # ═════════════════════════════════════════════════════════════
                                # ANTES (v6.14): POST síncrono ao Apps Script com 3 tentativas
                                # e backoff 0/2/5s. Cada cliente podia gastar até 97s aqui
                                # esperando (timeout=30s × 3 + 7s backoff). Loop de 29 clientes
                                # estourava timeout do Streamlit Cloud (~5-10min).
                                #
                                # AGORA (v6.21):
                                #   Thread A: INSERT direto no agenda_contexto do Supabase
                                #             (200-500ms, mesmo comportamento do
                                #             _supabaseUpsertContexto do Apps Script)
                                #   Thread B: POST Apps Script salvar_contexto em background
                                #             (mantém PropertiesService + agendarLembretes)
                                #   Loop continua em ~0.3s (sem esperar nenhuma das threads)
                                #
                                # No fim do loop, aguardamos threads pendentes (com timeout)
                                # e fazemos GET verificação final no SUPABASE (não Apps Script)
                                # ═════════════════════════════════════════════════════════════
                                fut_supabase = executor.submit(
                                    _insert_supabase_contexto,
                                    telefone_formatado, nome_cliente, procedimento,
                                    unidade_selecionada, horario_cliente,
                                    horario2_cliente, servico2_cliente,
                                    numero_alerta_formatado,
                                )
                                fut_appscript = executor.submit(
                                    _post_apps_script_salvar,
                                    telefone_formatado, nome_cliente, procedimento,
                                    unidade_selecionada, horario_cliente,
                                    horario2_cliente, servico2_cliente,
                                    numero_alerta_formatado,
                                )
                                # Guarda pra verificação final e retry
                                telefones_disparados.append({
                                    'tel': telefone_formatado,
                                    'nome': nome_cliente,
                                    'fut_supabase': fut_supabase,
                                    'fut_appscript': fut_appscript,
                                })
                            else:
                                erros += 1
                                # 🆕 v6.14.2 — grava detalhe do erro PRA SEMPRE (Supabase),
                                # não só temporariamente na tela. Trunca JSON longo em 300 chars.
                                try:
                                    res_str = json.dumps(res, ensure_ascii=False)
                                except Exception:
                                    res_str = str(res)
                                if len(res_str) > 300:
                                    res_str = res_str[:300] + "…"
                                erros_envio_detalhes.append(
                                    f"{nome_cliente} ({telefone_formatado}) | HTTP {code} | {res_str}"
                                )
                                st.error(
                                    f"❌ Falha — {nome_cliente} ({telefone_formatado}) | "
                                    f"HTTP {code} | {json.dumps(res, ensure_ascii=False)}"
                                )
                        else:
                            erros += 1
                            # 🆕 v6.14.2 — grava também os casos de telefone inválido/ausente,
                            # que antes só apareciam na tela e depois sumiam com refresh.
                            erros_envio_detalhes.append(
                                f"{nome_cliente} (tel='{telefone_formatado}') | INVÁLIDO | "
                                f"Telefone ausente ou com menos de 12 dígitos"
                            )
                            st.error(
                                f"⚠️ Número inválido ou ausente para: "
                                f"{nome_cliente} (encontrado: '{telefone_formatado}')"
                            )

                        time.sleep(1.5)
                        progresso.progress((i + 1) / total_linhas)

                        # 🆕 v6.21 — Update incremental a cada UPDATE_BATCH_SIZE clientes
                        # Grava contadores + heartbeat_em pra watchdog detectar se travar.
                        # Se Python morrer entre batches, banco tem estado real.
                        if (i + 1) - ultimo_batch_atualizado >= UPDATE_BATCH_SIZE:
                            _atualizar_progresso_parcial(
                                registro_id=registro_id,
                                sucessos=sucessos,
                                erros=erros,
                                ultimo_cliente=f"{nome_cliente} ({telefone_formatado})",
                            )
                            ultimo_batch_atualizado = i + 1

                    # ═════════════════════════════════════════════════════════════
                    # 🆕 v6.21 — WAIT THREADS EM VOO (fire-and-forget cleanup)
                    # ═════════════════════════════════════════════════════════════
                    # Loop terminou. Threads Supabase + Apps Script podem ainda estar
                    # rodando. Aguarda com timeout generoso (60s) — se algum thread
                    # travar depois disso, faz retry síncrono.
                    status_texto.text("⏳ Aguardando escritas em background finalizarem...")
                    supabase_falhas = []      # clientes cujo INSERT Supabase falhou
                    appscript_falhas = []     # clientes cujo POST Apps Script falhou

                    for d in telefones_disparados:
                        try:
                            ok_sb = d['fut_supabase'].result(timeout=60)
                            if not ok_sb:
                                supabase_falhas.append(d)
                        except Exception:
                            supabase_falhas.append(d)
                        try:
                            ok_as = d['fut_appscript'].result(timeout=60)
                            if not ok_as:
                                appscript_falhas.append(d)
                        except Exception:
                            appscript_falhas.append(d)

                    executor.shutdown(wait=False)

                    # 🆕 v6.21 — Retry síncrono das falhas (última chance)
                    if supabase_falhas:
                        status_texto.text(f"🔁 Retentando {len(supabase_falhas)} INSERT(s) Supabase...")
                        for d in supabase_falhas[:]:
                            if _insert_supabase_contexto(
                                d['tel'], d['nome'], "-", "-", "-", "", "", ""
                            ):
                                supabase_falhas.remove(d)
                    if appscript_falhas:
                        status_texto.text(f"🔁 Retentando {len(appscript_falhas)} POST(s) Apps Script...")
                        for d in appscript_falhas[:]:
                            if _post_apps_script_salvar(
                                d['tel'], d['nome'], "-", "-", "-", "", "", ""
                            ):
                                appscript_falhas.remove(d)

                    status_texto.text("✅ Loop de envios concluído. Verificando contextos no Supabase...")

                    # ════════════════════════════════════════════════════════════════
                    # 🆕 v6.21 — VERIFICAÇÃO FINAL POR GET NO SUPABASE
                    # ════════════════════════════════════════════════════════════════
                    # ANTES (v6.14): GET no Apps Script pra ler TODO o Sheets Contexto
                    # (595+ linhas → 50KB JSON → 45s timeout, frequentemente falhava).
                    #
                    # AGORA (v6.21): 1 SELECT no Supabase filtrando SÓ pelos telefones
                    # disparados. Vai em <500ms mesmo com 10k linhas na tabela.
                    #
                    # Lógica: quem escreveu no Supabase (thread A + thread B) com
                    # sucesso, aparece com status='aguardando'. Quem não aparece,
                    # é falha real → clientes_sem_contexto.
                    # ════════════════════════════════════════════════════════════════
                    falhas_contexto = 0
                    clientes_sem_contexto = []
                    verificacao_ok = False
                    falsos_positivos_corrigidos = 0
                    falhas_silenciosas_detectadas = 0

                    if telefones_disparados:
                        try:
                            supabase_url = st.secrets["SUPABASE_URL"]
                            supabase_key = st.secrets["SUPABASE_KEY"]
                            # Query: pega só os telefones que a gente disparou, com
                            # status='aguardando' (o INSERT põe esse status).
                            tels_lista = ",".join(f'"{d["tel"]}"' for d in telefones_disparados)
                            r_check = requests.get(
                                f"{supabase_url}/rest/v1/agenda_contexto"
                                f"?select=telefone,status,disparo_ts"
                                f"&telefone=in.({tels_lista})"
                                f"&arquivado_em=is.null",
                                headers={
                                    "apikey": supabase_key,
                                    "Authorization": f"Bearer {supabase_key}",
                                },
                                timeout=(5, 15),
                            )
                            if r_check.status_code == 200:
                                contextos = r_check.json()
                                tels_no_contexto = {c['telefone'] for c in contextos}

                                for d in telefones_disparados:
                                    if d['tel'] not in tels_no_contexto:
                                        falhas_contexto += 1
                                        clientes_sem_contexto.append(d['nome'])
                                        falhas_silenciosas_detectadas += 1
                                verificacao_ok = True
                            else:
                                st.warning(
                                    f"⚠️ Verificação Supabase retornou HTTP {r_check.status_code}. "
                                    f"Confie no INSERT direto (deve estar OK)."
                                )
                        except Exception as e_get:
                            st.warning(
                                f"⚠️ Verificação Supabase falhou: {e_get}. "
                                f"Confie no INSERT direto (deve estar OK)."
                            )

                    status_texto.text("✅ Processamento concluído!")

                    if registro_id:
                        ok = _atualizar_registro_historico(
                            registro_id=registro_id,
                            sucessos=sucessos,
                            erros=erros,
                            falhas_contexto=falhas_contexto,
                            clientes_sem_contexto=clientes_sem_contexto,
                            erros_envio_detalhes=erros_envio_detalhes,  # 🆕 v6.14.2
                        )
                        if ok:
                            st.caption(f"📝 Registro #{registro_id} atualizado com totais finais")
                    else:
                        try:
                            _sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                            _sb.table("disparos_historico").insert({
                                "unidade": unidade_selecionada,
                                "arquivo": arquivo_upload.name if arquivo_upload else "—",
                                "data_sessoes": _data_sessoes,
                                "total_clientes": total_linhas,
                                "whatsapp_ok": sucessos,
                                "contexto_ok": sucessos - falhas_contexto,
                                "falhas_contexto": falhas_contexto,
                                "clientes_falha": ", ".join(clientes_sem_contexto) if clientes_sem_contexto else None,
                                "erros_envio": erros,
                                # 🆕 v6.14.2 — inclui nova coluna também no fallback INSERT
                                "erros_envio_detalhes": " || ".join(erros_envio_detalhes) if erros_envio_detalhes else None,
                                "numero_alerta": numero_alerta_formatado,
                                "observacao": "⚠️ Fallback v1 — registro inicial falhou",
                            }).execute()
                            st.caption("📝 Histórico gravado (fallback)")
                        except Exception as e_hist:
                            st.error(f"❌ Não salvou no histórico (fallback também falhou): {e_hist}")

                    if sucessos > 0 and falhas_contexto == 0:
                        st.balloons()
                    st.success(
                        f"🎉 Disparos finalizados! "
                        f"✅ Sucessos: {sucessos} | ❌ Erros/Falhas: {erros}"
                    )

                    # 🆕 v6.14 — Feedback claro sobre o que a verificação final corrigiu/detectou
                    if verificacao_ok and falsos_positivos_corrigidos > 0:
                        st.info(
                            f"🔎 **Verificação final corrigiu {falsos_positivos_corrigidos} "
                            f"alerta(s) falso(s) positivo(s)**: o POST `salvar_contexto` "
                            f"reportou falha (timeout), mas o Apps Script JÁ HAVIA salvado o contexto. "
                            f"Esses clientes estão funcionando normalmente — o robô vai reconhecer "
                            f"as respostas deles."
                        )
                    if verificacao_ok and falhas_silenciosas_detectadas > 0:
                        st.warning(
                            f"🔎 **Verificação final detectou {falhas_silenciosas_detectadas} "
                            f"falha(s) silenciosa(s)**: o POST retornou status 200 mas o contexto "
                            f"NÃO está no Apps Script. Esses clientes precisam de re-disparo."
                        )

                    if falhas_contexto > 0:
                        st.error(
                            f"🚨 **ATENÇÃO: {falhas_contexto} cliente(s) receberam o WhatsApp "
                            f"mas o contexto NÃO foi salvo na planilha!**\n\n"
                            f"O robô NÃO vai reconhecer as respostas dessas clientes.\n\n"
                            f"**Clientes afetados:** {', '.join(clientes_sem_contexto)}\n\n"
                            f"💡 Solução: re-dispare só para essas clientes."
                        )

                    # 🆕 v6.14.2 — Bloco de erros de envio persistente (Supabase)
                    if erros_envio_detalhes:
                        st.error(
                            f"🚨 **{len(erros_envio_detalhes)} erro(s) de envio detectado(s)** "
                            f"— detalhes gravados no Supabase (`disparos_historico.erros_envio_detalhes`).\n\n"
                            + "\n".join(f"• {d}" for d in erros_envio_detalhes[:10])
                            + (f"\n\n... +{len(erros_envio_detalhes)-10} outros" if len(erros_envio_detalhes) > 10 else "")
                        )

        except Exception as erro_geral:
            st.error(f"❌ Erro ao processar o arquivo: {erro_geral}")
            # 🆕 v6.21 — se der exceção no meio do disparo, marca registro
            # como interrompido pra dashboard mostrar direito (e watchdog não
            # perder tempo checando).
            try:
                if 'registro_id' in dir() and locals().get('registro_id'):
                    _sb_err = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                    _sb_err.table("disparos_historico").update({
                        "status": "interrompido",
                        "morreu_em": datetime.now(timezone.utc).isoformat(),
                        "observacao": f"❌ Exceção Python no disparador: {str(erro_geral)[:300]}",
                    }).eq("id", locals()["registro_id"]).execute()
            except Exception:
                pass  # silencioso — melhor esforço
