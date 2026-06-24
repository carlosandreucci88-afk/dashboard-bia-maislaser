"""
==============================================================================
ABA 🚀 DISPARAR AGENDA — Robô Confirmação Agenda
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
from supabase import create_client

NOME_MODELO_MENSAGEM        = "confirmacao_agenda_maislaser_v4"
NOME_MODELO_MENSAGEM_2SESS  = "confirmacao_agenda_maislaser_2sessoes_v2"

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
        resposta = requests.post(url, headers=headers, json=payload)
        return resposta.status_code, resposta.json()
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
        resposta = requests.post(url, headers=headers, json=payload)
        return resposta.status_code, resposta.json()
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
        }).execute()
        if resp.data and len(resp.data) > 0:
            return resp.data[0].get("id")
        return None
    except Exception as e:
        st.warning(f"⚠️ Não consegui criar registro inicial no histórico: {e}")
        return None


def _atualizar_registro_historico(registro_id, sucessos, erros, falhas_contexto,
                                   clientes_sem_contexto):
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
            "observacao": None,
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
                    # 🆕 v6.14 — tracking de TODOS os telefones disparados (com sucesso na Meta).
                    # Será usado no GET final pra verificar se realmente estão no Contexto.
                    # Cada item: {tel, nome, ctx_post_ok}
                    telefones_disparados = []
                    total_linhas = len(df_agrupado)

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

                            if tem_2_sessoes:
                                code, res = enviar_mensagem_2sessoes(
                                    nome_cliente,
                                    horario_cliente, procedimento,
                                    horario2_cliente, servico2_cliente,
                                    unidade_selecionada, telefone_formatado
                                )
                            else:
                                code, res = enviar_mensagem_whatsapp(
                                    nome_cliente, horario_cliente,
                                    procedimento, unidade_selecionada,
                                    telefone_formatado
                                )

                            if code in (200, 201):
                                sucessos += 1
                                # FIX v6.14 (24/06/2026): timeout 30s + 3 tentativas + verificação GET final.
                                #
                                # CAUSA RAIZ DO BUG ANTERIOR (v6.13 — 15s, 2 tent, sem GET):
                                #   Apps Script `salvarContexto` demorava >15s pra responder em
                                #   condições de carga (lock contention + trigger paralelo + 595+
                                #   linhas no Contexto). Streamlit dava timeout e marcava como
                                #   falha — MAS o Apps Script já havia salvado o contexto.
                                #   Resultado: alerta falso positivo "Contexto NÃO salvo".
                                #   Caso real: disparo Suzano 24/06 11:53 reportou 5 falhas,
                                #   todas estavam no Contexto e 4 já tinham confirmado.
                                #
                                # FIX v6.14:
                                #   (1) timeout 30s (margem maior) + 3 tentativas (mais resiliente)
                                #   (2) NÃO mostra warning durante o loop (não confiável em tempo real)
                                #   (3) Acumula `telefones_disparados` pra verificação final via GET
                                #   (4) Após o loop, 1 GET único no endpoint=contexto compara TODOS os
                                #       telefones disparados com a lista REAL do Apps Script.
                                #       Isso elimina falso positivo definitivamente, e ainda detecta
                                #       falhas reais que o POST com status 200 + body de erro
                                #       teria mascarado.
                                ctx_post_ok = False
                                webhook_url = st.secrets.get("URL_WEBHOOK_CONTEXTO", "")
                                if webhook_url:
                                    ctx_payload = {
                                        "acao": "salvar_contexto",
                                        "telefone": telefone_formatado,
                                        "nome": nome_cliente,
                                        "servico": procedimento,
                                        "unidade": unidade_selecionada,
                                        "horario": horario_cliente,
                                        "horario2": horario2_cliente,
                                        "servico2": servico2_cliente,
                                        "numero_alerta": numero_alerta_formatado
                                    }
                                    # 3 tentativas com backoff 0s, 2s, 5s
                                    backoff = [0, 2, 5]
                                    for tentativa in range(3):
                                        if backoff[tentativa] > 0:
                                            time.sleep(backoff[tentativa])
                                        try:
                                            r_ctx = requests.post(webhook_url, json=ctx_payload, timeout=30)
                                            if r_ctx.status_code == 200:
                                                ctx_post_ok = True
                                                break
                                        except requests.exceptions.Timeout:
                                            # Timeout NÃO significa falha real — Apps Script pode
                                            # estar processando. Verificação final via GET vai dizer.
                                            continue
                                        except Exception:
                                            break
                                    if not ctx_post_ok:
                                        # POST falhou — pode ser falso positivo. Não exibe warning
                                        # ainda, só registra. Decisão final virá do GET de verificação.
                                        falhas_contexto_post += 1
                                        clientes_falha_post.append(nome_cliente)
                                # 🆕 v6.14 — guarda telefone disparado pra verificação final
                                telefones_disparados.append({
                                    'tel': telefone_formatado,
                                    'nome': nome_cliente,
                                    'ctx_post_ok': ctx_post_ok,
                                })
                            else:
                                erros += 1
                                st.error(
                                    f"❌ Falha — {nome_cliente} ({telefone_formatado}) | "
                                    f"HTTP {code} | {json.dumps(res, ensure_ascii=False)}"
                                )
                        else:
                            erros += 1
                            st.error(
                                f"⚠️ Número inválido ou ausente para: "
                                f"{nome_cliente} (encontrado: '{telefone_formatado}')"
                            )

                        time.sleep(1.5)
                        progresso.progress((i + 1) / total_linhas)

                    status_texto.text("✅ Loop de envios concluído. Verificando contextos no Apps Script...")

                    # ════════════════════════════════════════════════════════════════
                    # 🆕 v6.14 — VERIFICAÇÃO FINAL POR GET ÚNICO
                    # ════════════════════════════════════════════════════════════════
                    # Antes de gravar o resumo final, faz UMA chamada GET no endpoint
                    # `contexto` pra obter snapshot de TODOS os telefones no Contexto.
                    # Compara com `telefones_disparados`. Resultado:
                    #   • Falsos positivos do POST (timeout mas salvou OK) → corrigidos
                    #   • Falhas reais (POST OK mas não está no Contexto) → detectadas
                    #   • Falhas reais (POST falhou E não está no Contexto) → confirmadas
                    #
                    # Custo: 1 GET (~50KB pra 595 linhas) em vez de N GETs.
                    # Degradação graciosa: se GET falhar, usa dados do POST (comportamento v6.13).
                    # ════════════════════════════════════════════════════════════════
                    falhas_contexto = falhas_contexto_post  # default: usa POST se GET falhar
                    clientes_sem_contexto = list(clientes_falha_post)  # cópia defensiva
                    falsos_positivos_corrigidos = 0
                    falhas_silenciosas_detectadas = 0
                    verificacao_ok = False

                    if telefones_disparados:
                        try:
                            apps_script_url = st.secrets.get("APPS_SCRIPT_URL", "")
                            apps_script_token = st.secrets.get("APPS_SCRIPT_TOKEN", "")
                            if apps_script_url and apps_script_token:
                                # Dá um respiro pro Apps Script terminar de processar
                                # POSTs em fila (de timeouts pendentes).
                                # 🆕 v6.14.1 — 15s (era 5s) pra cobrir pior caso de lock contention:
                                # caso real DANIELI 24/06 levou 12min entre POST e salvar terminar.
                                # 15s não cobre 12min, mas dá margem pra fila do Apps Script drenar
                                # os POSTs que ainda estavam processando quando o loop acabou.
                                # Sem isso, GET pode rodar antes do salvar terminar e marcar
                                # falsos positivos como "falha real silenciosa".
                                time.sleep(15)
                                status_texto.text("🔎 Consultando Apps Script para verificar contextos...")
                                r_check = requests.get(
                                    f"{apps_script_url}?endpoint=contexto&token={apps_script_token}",
                                    timeout=45,  # GET pode ser mais lento com Contexto grande
                                )
                                if r_check.status_code == 200:
                                    ctx_data = r_check.json()
                                    linhas_ctx = ctx_data.get("linhas", [])
                                    # Set de telefones presentes no Contexto (string normalizada)
                                    tels_no_contexto = set()
                                    for linha_ctx in linhas_ctx:
                                        tel_ctx = str(linha_ctx.get("telefone", "")).strip()
                                        # remove sufixo .0 se vier como float
                                        if tel_ctx.endswith('.0'):
                                            tel_ctx = tel_ctx[:-2]
                                        if tel_ctx:
                                            tels_no_contexto.add(tel_ctx)

                                    # Recalcula falhas REAIS baseado no GET
                                    falhas_contexto = 0
                                    clientes_sem_contexto = []
                                    for d in telefones_disparados:
                                        no_contexto = d['tel'] in tels_no_contexto
                                        if no_contexto and not d['ctx_post_ok']:
                                            # Falso positivo do POST — salvou OK mas POST não soube
                                            falsos_positivos_corrigidos += 1
                                        elif not no_contexto and d['ctx_post_ok']:
                                            # POST disse OK mas não tá no Contexto — falha silenciosa
                                            falhas_silenciosas_detectadas += 1
                                            falhas_contexto += 1
                                            clientes_sem_contexto.append(d['nome'])
                                        elif not no_contexto and not d['ctx_post_ok']:
                                            # Confirmado: POST falhou E não tá no Contexto
                                            falhas_contexto += 1
                                            clientes_sem_contexto.append(d['nome'])
                                    verificacao_ok = True
                                else:
                                    st.warning(
                                        f"⚠️ Verificação final via GET retornou HTTP {r_check.status_code}. "
                                        f"Usando dados do POST (pode ter falsos positivos)."
                                    )
                            else:
                                st.info(
                                    "ℹ️ Verificação final pulada: secrets APPS_SCRIPT_URL ou "
                                    "APPS_SCRIPT_TOKEN não configurados. Usando dados do POST."
                                )
                        except Exception as e_get:
                            st.warning(
                                f"⚠️ Verificação final via GET falhou: {e_get}. "
                                f"Usando dados do POST (pode ter falsos positivos)."
                            )

                    status_texto.text("✅ Processamento concluído!")

                    if registro_id:
                        ok = _atualizar_registro_historico(
                            registro_id=registro_id,
                            sucessos=sucessos,
                            erros=erros,
                            falhas_contexto=falhas_contexto,
                            clientes_sem_contexto=clientes_sem_contexto,
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

        except Exception as erro_geral:
            st.error(f"❌ Erro ao processar o arquivo: {erro_geral}")
