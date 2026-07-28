"""
==============================================================================
ABA 🚀 DISPARAR AGENDA — v7.1 (28/07/2026)
==============================================================================
O DISPARO DEIXOU DE ACONTECER NESTA TELA.

Antes: o navegador era o motor. O loop enviava os templates um a um dentro da
sessão do Streamlit. Fechar a aba, cair a internet ou o Streamlit Cloud
reiniciar matava o disparo no meio.

Agora: esta tela só ENFILEIRA e ENCERRA. Quem envia é o worker do Apps Script
(fila_worker.gs), num trigger de 1min. Pode fechar a aba na cara — o disparo
segue. Esta página vira MONITOR: lê o progresso do banco.

──────────────────────────────────────────────────────────────────────────────
O QUE MUDOU EM RELAÇÃO AO v6.25

REMOVIDO (virou trabalho do worker):
  • enviar_mensagem_whatsapp        — POST na Meta
  • _insert_supabase_contexto       — agora vem do salvarContexto do worker
  • _post_apps_script_salvar        — idem
  • ThreadPoolExecutor / futures    — não há mais concorrência no navegador
  • retry síncrono com "-" literal  — o BUG-06 morre junto com o código
  • heartbeat guard em thread       — o worker bate heartbeat via RPC
  • verificação final por GET       — o progresso vem do banco, ao vivo

MANTIDO INTEGRALMENTE (é código testado em produção, não mexi):
  • limpar_numero, limpar_nome_cliente, limpar_nome_servico
    ↳ extraídos LITERALMENTE do v6.25 via AST, sem reescrita. A limpeza de
      CPF/telefone (hardening LGPD do v6.23) não podia sofrer drift.
  • validação de colunas, filtro 'Agendado', agrupamento por telefone/horário
  • seleção de unidade com dupla confirmação e ativação de alertas

NOVO:
  • _enfileirar()      — 1 chamada RPC atômica
  • _painel_progresso()— monitor lendo disparos_historico + disparos_fila_clientes

──────────────────────────────────────────────────────────────────────────────
POR QUE UMA RPC ÚNICA E NÃO INSERTs SEPARADOS

fila_enfileirar_disparo() cria o registro em disparos_historico E as N linhas
da fila numa transação só. Se fossem dois INSERTs e o Python morresse entre
eles, sobraria um disparo 'fila' com zero clientes: o worker o ignoraria (sem
cliente despachável) e o watchdog também (status não é 'em_andamento').
Fantasma silencioso no dashboard.

A RPC também tem guarda de duplicidade: mesmo arquivo + unidade + data dentro
de 10min devolve o disparo existente em vez de enfileirar de novo. É o caso
real de 13/07, quando um rerun do Streamlit criou o registro fantasma id=65.

──────────────────────────────────────────────────────────────────────────────
PRÉ-REQUISITOS
  1. fila_hardening_v2.sql aplicada          (feito)
  2. fila_enfileirar_disparo criada          (feito)
  3. fila_worker.gs colado no projeto Agenda
  4. criarTriggerWorkerFila() rodado 1x      ← SEM ISSO A FILA NÃO ANDA
==============================================================================
"""

import streamlit as st
import pandas as pd
import requests
import re
from datetime import datetime, timezone

NOME_MODELO_MENSAGEM = "confirmacao_agenda_maislaser_v4"  # quem usa é o worker


# ============================================================================
# HIGIENIZAÇÃO — extraída literalmente do v6.25 em produção
# ============================================================================
def limpar_numero(numero):
    """
    Normaliza telefone pro formato Meta (55 + DDD + número).
    v6.22 (BUG-04): trata dígito lixo no fim (14 dígitos terminando em 0).

    Casos válidos de saída:
      • 5511987654321  (13 dígitos: 55 + DDD 2 + 9 dígitos celular)
      • 551133334444   (12 dígitos: 55 + DDD 2 + 8 dígitos fixo)

    Casos que o UNO gera com lixo:
      • 55119537276720 (14 díg terminando em 0) → 5511953727672
      • número sem 55  → prefixa 55
      • .0 do float    → remove
    """
    if pd.isna(numero):
        return None
    num_str = str(numero).strip()
    if num_str.endswith('.0'):
        num_str = num_str[:-2]
    num_limpo = re.sub(r'\D', '', num_str)
    if num_limpo == '':
        return None

    # Garante prefixo 55
    if not num_limpo.startswith('55'):
        num_limpo = '55' + num_limpo

    # 🆕 BUG-04 FIX: detecta dígito lixo no fim
    # Telefone BR válido com 55: 12 díg (fixo) ou 13 díg (celular com 9).
    # Se vier 14 díg terminando em 0, é o bug de exportação do UNO — remove o 0.
    if len(num_limpo) == 14 and num_limpo.endswith('0'):
        num_limpo = num_limpo[:-1]

    # Se ainda tiver 14+ dígitos (outro tipo de lixo), tenta cortar pra 13
    # mas só se os primeiros 13 formarem número plausível (55 + DDD 11-99)
    if len(num_limpo) > 13:
        candidato = num_limpo[:13]
        ddd = candidato[2:4]
        if ddd.isdigit() and 11 <= int(ddd) <= 99:
            num_limpo = candidato

    return num_limpo

def limpar_nome_cliente(nome):
    """
    Higieniza nome do cliente antes de renderizar no template.

    v6.22 (BUG-04) — versão inicial:
      • Remove prefixo __ / _
      • Remove CPF com prefixo "Cpf: NNN.NNN.NNN-NN"
      • Remove telefone parentético (11) 99999-9999
      • Remove anotações (pago, finan, pede, obs, nota)

    v6.23 (09/07/2026) — LGPD hardening:
      • Remove CPF em QUALQUER forma NNN.NNN.NNN-NN mesmo sem prefixo
        (fecha vaza LGPD: "Rita (663.882.350-77)", "Cristiano ( Cpf Pl 418...)")
      • Remove asteriscos * / ** no fim ou meio do nome
      • Amplia captura de anotações: adiciona "pl", "PL", "cpf", "vide", "vindi"
      • Remove parênteses vazios/só espaços que sobram após limpeza de CPF
      • Remove hífen órfão no fim ("Andréia -")
      • Preserva apelidos legítimos "Maria (Bia)" (só remove parênteses
        contendo palavras-chave conhecidas de lixo, não parênteses arbitrários)

    Casos limpos com sucesso (validados sobre 662 linhas do Contexto real):
      • '__maria Augusta'                        → 'maria Augusta'
      • 'Marisa Bueno Cpf: 231.073.738-00'       → 'Marisa Bueno'
      • 'Rita De Cassia (663.882.350-77)'        → 'Rita De Cassia'  [LGPD]
      • 'Cristiano ( Cpf Pl 418.302.808-64)'     → 'Cristiano'       [LGPD]
      • 'Andréia -  (CPF 593.705.918-26 PL)'     → 'Andréia'         [LGPD]
      • 'Bruna Heloísa*'                         → 'Bruna Heloísa'
      • 'Tatiana Alcaraz**'                      → 'Tatiana Alcaraz'
      • 'Rebeca Dias* (vindi)'                   → 'Rebeca Dias'
      • 'Isabelly ((11)96648-4870)'              → 'Isabelly'
      • 'leila (pago 859.158.660-34)'            → 'leila'
    """
    if pd.isna(nome) or str(nome).strip() == '':
        return ''
    s = str(nome).strip()

    # 1. Prefixo de underscores
    s = s.lstrip('_').strip()

    # 2. CPF em qualquer contexto — NNN.NNN.NNN-NN (formato brasileiro completo)
    # Match agressivo sem exigir prefixo "cpf" antes: fecha vaza LGPD onde
    # o CPF vinha entre parênteses sem rótulo.
    s = re.sub(r'\s*\d{3}\.\d{3}\.\d{3}[-.\s]?\d{2}', '', s).strip()

    # 3. CPF sem pontos (11 dígitos) — só se precedido por "cpf" para não
    # apagar telefones acidentalmente
    s = re.sub(r'\s*[Cc][Pp][Ff]\s*[Pp]?[Ll]?\s*\d{11}', '', s).strip()

    # 4. Telefone parentético — captura ((11)99999-9999) e (11999999999)
    s = re.sub(r'\s*\(+\s*\d{2}\s*\)?\s*\d{4,5}[-.\s]?\d{4}\s*\)+', '', s).strip()
    s = re.sub(r'\s*\(\d{10,11}\)', '', s).strip()

    # 5. Anotações entre parênteses com marcadores conhecidos
    # (pago, finan, pede, obs, nota, vide, vindi, pl/PL, cpf/CPF)
    s = re.sub(
        r'\s*\(\s*(?:pl|cpf|obs|vide|vindi|pago|finan|pede|nota)[^)]{0,25}\)?',
        '', s, flags=re.IGNORECASE
    ).strip()

    # 6. Parênteses vazios resultantes de remoção acima
    s = re.sub(r'\s*\(\s*\)', '', s).strip()

    # 7. Asteriscos (final, meio, ou repetidos)
    s = re.sub(r'\*+', '', s).strip()

    # 8. Hífen órfão no fim ("Andréia -")
    s = re.sub(r'\s*-+\s*$', '', s).strip()

    # 9. Colapsa espaços múltiplos
    s = re.sub(r'\s+', ' ', s).strip()

    return s

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


# ============================================================================
# SUPABASE
# ============================================================================

def _sb_headers():
    key = st.secrets["SUPABASE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def _rpc(nome, params, timeout=(5, 30)):
    """POST numa RPC. Retorna (ok, json, mensagem_de_erro)."""
    try:
        r = requests.post(
            f"{st.secrets['SUPABASE_URL']}/rest/v1/rpc/{nome}",
            headers=_sb_headers(), json=params or {}, timeout=timeout,
        )
        if r.status_code in (200, 201, 204):
            try:
                return True, r.json(), None
            except Exception:
                return True, None, None
        return False, None, f"HTTP {r.status_code} — {r.text[:300]}"
    except Exception as e:
        return False, None, str(e)


def _get(path, timeout=(5, 20)):
    try:
        r = requests.get(f"{st.secrets['SUPABASE_URL']}/rest/v1/{path}",
                         headers=_sb_headers(), timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def _enfileirar(unidade, arquivo, data_sessoes, numero_alerta, clientes):
    """
    Chamada ATÔMICA: cria o histórico e enfileira os N clientes de uma vez.
    Retorna (ok, disparo_id, enfileirados, ja_existia, erro).
    """
    ok, data, err = _rpc("fila_enfileirar_disparo", {
        "p_unidade": unidade,
        "p_arquivo": arquivo,
        "p_data_sessoes": data_sessoes,
        "p_numero_alerta": numero_alerta,
        "p_clientes": clientes,
    }, timeout=(5, 60))
    if not ok:
        return False, None, 0, False, err
    linha = data[0] if isinstance(data, list) and data else data
    if not linha:
        return False, None, 0, False, "RPC não devolveu linha"
    return (True, linha.get("disparo_id"), linha.get("enfileirados", 0),
            bool(linha.get("ja_existia")), None)


# ============================================================================
# PAINEL DE PROGRESSO — lê do banco, não do navegador
# ============================================================================
# É isto que permite fechar a aba: o estado do disparo vive no Postgres.
# Reabrir a página em qualquer máquina mostra o mesmo número.
#
# Atualização MANUAL de propósito. O dashboard roda Streamlit 1.35, sem
# @st.fragment — auto-refresh forçaria rerun da página inteira a cada ciclo.
# ============================================================================

_ROTULO = {
    "fila":         ("🕓", "Na fila — worker pega em até 1min"),
    "em_andamento": ("🔄", "Enviando"),
    "concluido":    ("✅", "Concluído"),
    "interrompido": ("⚠️", "Interrompido — o worker retoma sozinho"),
}


def _painel_progresso(titulo="📡 Disparos em andamento"):
    """
    Mostra disparos abertos com contagem real da fila. Retorna quantos achou.

    Usa a RPC fila_painel() em vez de consultar disparos_historico direto.
    Motivo: filtrar por status IN ('fila','em_andamento','interrompido') no
    PostgREST trazia TODO disparo síncrono que o watchdog matou semanas atrás
    (9 registros de 14 a 17/07). Eles não têm linha em disparos_fila_clientes
    e nunca vão andar — a RPC faz JOIN com a fila, então ficam de fora.

    A RPC também traz as contagens agregadas: antes era 1 query pro histórico
    + 1 por disparo. Agora é uma chamada só.
    """
    ok, abertos, _err = _rpc("fila_painel", {"p_limite": 10})
    if not ok or not abertos:
        return 0

    st.markdown(f"### {titulo}")
    for d in abertos:
        did = d["id"]
        icone, texto = _ROTULO.get(d["status"], ("•", d["status"]))
        total = d.get("total") or 0
        enviados = d.get("enviados", 0)
        erros = d.get("erros", 0)
        pendentes = d.get("pendentes", 0)
        processando = d.get("processando", 0)
        feitos = enviados + erros

        st.markdown(
            f"**{icone} #{did} · {d.get('unidade','—')} · {texto}**  \n"
            f"`{d.get('arquivo','—')}` · sessões {d.get('data_sessoes','—')}"
        )
        if total > 0:
            st.progress(min(feitos / total, 1.0))
        st.caption(
            f"✅ {enviados} enviados · ❌ {erros} erros · "
            f"⏳ {pendentes} na fila · 🔄 {processando} em voo · de {total}"
        )

        # Um estado, uma mensagem. Antes as duas condições disparavam juntas e
        # a tela dizia "fila zerada" e "ainda há fila pendente" ao mesmo tempo.
        if pendentes == 0 and processando == 0:
            st.info("Fila zerada. O worker fecha o disparo na próxima passada.")
        elif d["status"] == "interrompido":
            st.warning(
                "Marcado como interrompido, mas ainda há fila pendente — "
                "o worker retoma sozinho. Nenhuma ação necessária."
            )

        # Erros detalhados, só se houver
        if erros:
            with st.expander(f"❌ {erros} cliente(s) com erro definitivo"):
                falhos = _get(
                    f"disparos_fila_clientes?disparo_id=eq.{did}&status=eq.erro"
                    "&select=nome,telefone,tentativas,erro_detalhe&order=ordem"
                ) or []
                if falhos:
                    st.dataframe(pd.DataFrame(falhos), hide_index=True,
                                 use_container_width=True)
                st.caption(
                    "Essas clientes NÃO receberam o WhatsApp. Precisam de "
                    "contato manual ou de um novo disparo só pra elas."
                )
        st.divider()

    if st.button("🔄 Atualizar progresso", key="fila_refresh"):
        st.rerun()
    return len(abertos)


# ============================================================================
# ABA
# ============================================================================

def render_aba_disparador():
    # Monitor primeiro: se há disparo rodando, é a informação mais importante
    # da tela — inclusive pra quem acabou de reabrir o navegador.
    abertos = _painel_progresso()
    if abertos:
        st.divider()

    st.markdown("### 🚀 Novo disparo")

    unidade_selecionada = st.selectbox(
        "Selecione a Unidade que está operando hoje:",
        ["", "Mogi das Cruzes", "Suzano"], index=0,
    )
    if not unidade_selecionada:
        st.info("👆 Selecione a unidade acima para continuar.")
        return

    st.warning(f"⚠️ Você selecionou a unidade **{unidade_selecionada}** — está correto?")
    col1, col2 = st.columns(2)
    with col1:
        confirmar = st.button(f"✅ Sim, é {unidade_selecionada}",
                              use_container_width=True, key="disp_btn_confirmar_unidade")
    with col2:
        corrigir = st.button("❌ Não, corrigir", use_container_width=True,
                             key="disp_btn_corrigir_unidade")

    if corrigir:
        st.error("⬆️ Corrija a unidade no campo acima antes de continuar.")
        return
    if not confirmar and not st.session_state.get("disp_unidade_confirmada"):
        return
    if confirmar:
        st.session_state["disp_unidade_confirmada"] = True
        st.session_state["disp_unidade_valor"] = unidade_selecionada

    unidade_selecionada = st.session_state.get("disp_unidade_valor", unidade_selecionada)
    st.success(f"✅ Unidade **{unidade_selecionada}** confirmada!")

    numero_alerta_input = st.text_input(
        "Digite o número de WhatsApp que receberá os alertas (com DDD):", value=""
    )
    if not numero_alerta_input:
        return

    numero_alerta_formatado = limpar_numero(numero_alerta_input)
    st.info(f"📢 Os alertas serão enviados para: {numero_alerta_formatado}")

    numero_robo = st.secrets["NUMERO_ROBO_ALERTAS"]
    st.markdown(
        f"""
        <a href="https://wa.me/{numero_robo}?text=oi" target="_blank" style="
            display:inline-block;background-color:#25D366;color:white;
            padding:10px 20px;border-radius:8px;text-decoration:none;
            font-weight:bold;font-size:15px;margin-top:4px;">
        📲 Clique aqui para ativar os alertas no seu WhatsApp</a>
        <p style="font-size:12px;color:gray;margin-top:6px;">
        ⚠️ Envie o "oi" antes de enfileirar para receber os alertas.</p>
        """,
        unsafe_allow_html=True,
    )
    if not st.checkbox("✅ Já enviei o 'oi' e estou pronto!", key="disp_alertas_ativados"):
        return

    arquivo_upload = st.file_uploader("Selecione a planilha do UNO (.xlsx)", type=["xlsx"])
    if arquivo_upload is None:
        return

    try:
        df = pd.read_excel(arquivo_upload)

        # ── Validação de colunas ──
        essenciais = ["Data", "Cliente", "Telefone", "Serviço"]
        faltando = [c for c in essenciais if c not in df.columns]
        st.subheader("🔍 Validação da Planilha")
        if faltando:
            st.error(
                "🚫 **Disparo bloqueado.** Coluna(s) ausente(s): "
                + ", ".join(faltando)
            )
            return
        st.success("✅ Todas as colunas essenciais foram encontradas.")

        extras = [c for c in df.columns if c not in essenciais]
        if extras:
            st.caption(f"ℹ️ Colunas extras ignoradas: {', '.join(extras)}")

        # ── Filtro de situação ──
        if "Situação" in df.columns:
            antes = len(df)
            df = df[df["Situação"].str.strip().str.lower() == "agendado"]
            if antes - len(df) > 0:
                st.warning(
                    f"⚠️ {antes - len(df)} registro(s) ignorado(s) por não estarem "
                    f"com situação 'Agendado' (cancelados, faltou, etc.)."
                )

        # ── Higienização + agrupamento (idêntico ao v6.25) ──
        df["Cliente"] = df["Cliente"].apply(limpar_nome_cliente)
        df["Telefone"] = df["Telefone"].apply(limpar_numero).fillna("").astype(str)
        df["Serviço"] = df["Serviço"].apply(limpar_nome_servico)
        df["Horario"] = pd.to_datetime(df["Data"]).dt.strftime("%d/%m/%Y às %Hh%M")
        df = df[df["Serviço"] != ""]

        df_nomes = df.groupby("Telefone")["Cliente"].first().reset_index()
        df_srv_h = (df.groupby(["Telefone", "Horario"])["Serviço"]
                      .apply(lambda x: ", ".join(sorted(set(x)))).reset_index())
        df_hor = df.groupby("Telefone")["Horario"].apply(lambda x: sorted(set(x))).reset_index()
        df_hor["Horario2"] = df_hor["Horario"].apply(lambda x: x[1] if len(x) > 1 else "")
        df_hor["Horario"] = df_hor["Horario"].apply(lambda x: x[0])
        df_s1 = df_srv_h.groupby("Telefone").first().reset_index()[["Telefone", "Serviço"]]
        df_s2 = (df_srv_h.groupby("Telefone")
                 .apply(lambda x: x.iloc[1]["Serviço"] if len(x) > 1 else "")
                 .reset_index().rename(columns={0: "Servico2"}))

        g = (df_s1.merge(df_nomes, on="Telefone")
                  .merge(df_hor[["Telefone", "Horario", "Horario2"]], on="Telefone")
                  .merge(df_s2, on="Telefone", how="left"))
        g["Servico2"] = g["Servico2"].fillna("")
        g = g[["Cliente", "Serviço", "Telefone", "Horario", "Horario2", "Servico2"]]

        # ── Telefones inválidos: fora da fila, avisados na tela ──
        invalidos = g[g["Telefone"].str.len() < 12]
        g = g[g["Telefone"].str.len() >= 12]
        if not invalidos.empty:
            st.error(
                f"⚠️ **{len(invalidos)} cliente(s) com telefone inválido — "
                f"NÃO serão enfileirados:**\n\n"
                + "\n".join(f"• {r['Cliente']} (`{r['Telefone']}`)"
                            for _, r in invalidos.iterrows())
            )

        if g.empty:
            st.error("🚫 Nenhum cliente válido para enfileirar.")
            return

        st.info(f"🔄 Agrupamento concluído: **{len(g)}** cliente(s) para disparo.")
        st.subheader(f"Pré-visualização ({unidade_selecionada}):")
        st.dataframe(g, use_container_width=True)

        data_sessoes = str(g.iloc[0]["Horario"]).split(" às ")[0]

        st.divider()
        if st.button("📥 Enfileirar disparos", key="disp_btn_enfileirar",
                     type="primary", use_container_width=True):

            clientes = [{
                "telefone": str(r["Telefone"]),
                "nome":     str(r["Cliente"]),
                "servico":  str(r["Serviço"]),
                "horario":  str(r["Horario"]),
                "horario2": str(r["Horario2"] or ""),
                "servico2": str(r["Servico2"] or ""),
            } for _, r in g.iterrows()]

            with st.spinner("Enfileirando..."):
                ok, did, n, ja, err = _enfileirar(
                    unidade_selecionada,
                    arquivo_upload.name,
                    data_sessoes,
                    numero_alerta_formatado,
                    clientes,
                )

            if not ok:
                st.error(
                    f"❌ **Não consegui enfileirar.** Nada foi criado — "
                    f"pode tentar de novo com segurança.\n\n`{err}`"
                )
                return

            if ja:
                st.warning(
                    f"⚠️ Este arquivo já foi enfileirado há pouco "
                    f"(disparo **#{did}**, {n} clientes). Não enfileirei de novo "
                    f"para não duplicar as mensagens."
                )
            else:
                st.success(f"✅ **{n} cliente(s) enfileirado(s)** — disparo **#{did}**")
                st.balloons()

            st.info(
                "**Pode fechar esta aba.** O envio é feito pelo robô no servidor, "
                "a cada 1 minuto. Se a internet cair ou o navegador fechar, o "
                "disparo continua do ponto onde parou.\n\n"
                "Volte a esta tela quando quiser para acompanhar o progresso."
            )
            st.session_state["disp_alertas_ativados"] = False

    except Exception as e:
        st.error(f"❌ Erro ao processar o arquivo: {e}")
