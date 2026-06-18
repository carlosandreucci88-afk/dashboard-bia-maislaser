"""
==============================================================================
ABA HISTÓRICO BIA — Lotes que a Bia v5 puxou (modo AUTO)
==============================================================================

Mostra TODOS os lotes que a Bia trabalhou (ou está trabalhando) em modo AUTO.
Filtra por `bia_puxou_em IS NOT NULL` no Apps Script Z-API e cruza com
`bia_disparos` no Supabase pra contar disparos/respostas em tempo real.

Sub-abas:
  📦 Lotes (resumo)      — tabela 1 linha por campanha + filtros
  🔍 Detalhes do lote    — clica num lote → vê os 20+ indicados individualmente

Status final de cada lote:
  🤖 RODANDO              — bia_puxou_em preenchido, validacao_marcada vazia
  ✅ AUTO_VALIDADO_BIA    — atingiu 30% de respostas em <36h
  ❌ AUTO_INVALIDADO_BIA  — timeout 36h sem atingir 30%

Fontes:
  • Apps Script Z-API endpoint /?endpoint=validacao  → metadados da campanha
  • Apps Script Z-API endpoint /?endpoint=clientes   → bia_puxou_em (histórico)
  • Apps Script Z-API endpoint /?endpoint=contatos_cliente&campanha_id=X
  • Supabase tabela bia_disparos                     → status de cada disparo
==============================================================================
"""

import streamlit as st
import pandas as pd
import math
from datetime import datetime, timedelta, timezone

# Reusa helpers do aba_zapi.py — não duplica código
from aba_zapi import (
    _zapi_get,
    _parse_iso,
    _humanizar_tempo,
    _formatar_telefone,
    _mostrar_erro_e_parar,
    _meta_respostas,
    _get_supabase_zapi,
)

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# CARGA DE DADOS — combina Apps Script (lotes) + Supabase (disparos)
# ============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def _carregar_lotes_bia():
    """
    Carrega TODOS os lotes onde a Bia puxou (modo=AUTO + bia_puxou_em preenchido).
    Inclui lotes ATIVOS (em AGUARDANDO_VALIDACAO) e ARQUIVADOS (já finalizados).

    Estratégia:
    1. Pega ativos via /?endpoint=validacao (filtra bia_puxou_em IS NOT NULL)
    2. Pega TODOS clientes via /?endpoint=clientes (filtra BIA_PUXOU_EM IS NOT NULL)
    3. Pega arquivados via /?endpoint=clientes com fonte_arquivo=true (se suportado)
    
    Por enquanto: SÓ ATIVOS via endpoint validacao (cobre 99% dos casos do MVP).
    Quando tu finalizar muitos lotes e quiser ver histórico antigo, a gente
    adiciona suporte ao arquivo num passo seguinte.
    """
    data = _zapi_get("validacao")
    if isinstance(data, dict) and data.get("_erro"):
        return pd.DataFrame(), data.get("_erro")

    linhas = data.get("linhas", [])
    if not linhas:
        return pd.DataFrame(), None

    df = pd.DataFrame(linhas)

    # Filtra SÓ os que a Bia puxou (modo=AUTO + bia_puxou_em preenchido)
    df["modo"] = df.get("modo", pd.Series([""] * len(df))).fillna("").astype(str).str.upper().str.strip()
    df["bia_puxou_em_dt"] = df.get("bia_puxou_em", pd.Series([None] * len(df))).apply(_parse_iso)
    df["validacao_marcada"] = df["validacao_marcada"].fillna("").astype(str).str.upper().str.strip()

    df = df[(df["modo"] == "AUTO") & df["bia_puxou_em_dt"].notna()].copy()

    if df.empty:
        return pd.DataFrame(), None

    # Status derivado pra cada lote
    def _status_final(row):
        marcada = row["validacao_marcada"]
        if marcada == "AUTO_VALIDADO_BIA":
            return ("✅ AUTO_VALIDADO", "ok")
        if marcada == "AUTO_INVALIDADO_BIA":
            return ("❌ AUTO_INVALIDADO", "alerta")
        if marcada == "VALIDADO":
            return ("✅ VALIDADO (manual)", "ok")
        if marcada == "INVALIDADO":
            return ("❌ INVALIDADO (manual)", "alerta")
        # Sem decisão final ainda — Bia rodando
        # Checa timeout
        agora = datetime.now(TZ_SP)
        horas = (agora - row["bia_puxou_em_dt"]).total_seconds() / 3600
        if horas >= 36:
            return ("⏰ TIMEOUT (aguardando cron)", "alerta")
        return ("🤖 RODANDO", "info")

    df[["_status_label", "_status_class"]] = df.apply(
        lambda r: pd.Series(_status_final(r)), axis=1
    )

    # Tempo decorrido desde Bia puxar
    agora = datetime.now(TZ_SP)
    df["horas_rodando"] = (agora - df["bia_puxou_em_dt"]).dt.total_seconds() / 3600

    return df, None


@st.cache_data(ttl=30, show_spinner=False)
def _contar_disparos_por_status(campanha_ids_tuple):
    """
    Agrupa bia_disparos por (campanha_id, status). Retorna dict aninhado:
      { campanha_id: { status: count, ... }, ... }
    Cache 30s.
    """
    if not campanha_ids_tuple:
        return {}
    try:
        sb = _get_supabase_zapi()
        result = (
            sb.table("bia_disparos")
            .select("campanha_id, status, respondeu_em")
            .in_("campanha_id", list(campanha_ids_tuple))
            .execute()
        )
        STATUS_EFETIVAMENTE_DISPARADO = {"DISPARADO", "RESPONDEU", "IGNOROU", "SKIP_BASE",
                                          "ERRO_NUMERO_INVALIDO", "BLOQUEADO_PELO_INDICADO"}
        contagem = {}
        for row in result.data or []:
            cid = row.get("campanha_id")
            if not cid:
                continue
            if cid not in contagem:
                contagem[cid] = {"_total": 0, "_respondeu": 0, "_disparados_real": 0}
            status = (row.get("status") or "DESCONHECIDO").upper()
            contagem[cid][status] = contagem[cid].get(status, 0) + 1
            contagem[cid]["_total"] += 1
            if status in STATUS_EFETIVAMENTE_DISPARADO:
                contagem[cid]["_disparados_real"] += 1
            if row.get("respondeu_em"):
                contagem[cid]["_respondeu"] += 1
        return contagem
    except Exception as e:
        st.toast(f"⚠️ Erro Supabase: {e}", icon="⚠️")
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def _carregar_disparos_de_uma_campanha(campanha_id):
    """
    Carrega os disparos individuais de UMA campanha pra drill-down.
    Retorna DataFrame com colunas: telefone, nome (se tiver), status,
    disparado_em, respondeu_em, primeira_msg.
    """
    if not campanha_id:
        return pd.DataFrame()
    try:
        sb = _get_supabase_zapi()
        result = (
            sb.table("bia_disparos")
            .select("*")
            .eq("campanha_id", campanha_id)
            .order("disparado_em", desc=False, nullsfirst=False)
            .execute()
        )
        df = pd.DataFrame(result.data or [])
        return df
    except Exception as e:
        st.error(f"❌ Erro ao carregar disparos: {e}")
        return pd.DataFrame()


# ============================================================================
# ENTRY POINT principal — tabs internas
# ============================================================================

def render_aba_historico_bia():
    st.markdown("## 📜 Histórico Bia")
    st.caption(
        "Lotes que a Bia v5 trabalhou (modo AUTO). "
        "Conta disparos e respostas em tempo real cruzando Apps Script Z-API "
        "com Supabase `bia_disparos`."
    )

    # Carga inicial
    df_lotes, erro = _carregar_lotes_bia()
    if erro:
        st.error(f"❌ {erro}")
        return

    if df_lotes.empty:
        st.info(
            "📭 **Nenhum lote da Bia ainda.**\n\n"
            "Quando a coordenadora marcar uma campanha como **🤖 AUTO** e o "
            "Cron 6 v2 puxar o lote, vai aparecer aqui o histórico de "
            "disparos e respostas em tempo real."
        )
        return

    # Cruza com Supabase pra ter contagem de disparos
    camp_ids = tuple(df_lotes["campanha_id"].dropna().tolist())
    contagem = _contar_disparos_por_status(camp_ids)

    # Enriquece o df com contagens
    df_lotes["total_disparados_supabase"] = df_lotes["campanha_id"].apply(
        lambda cid: contagem.get(cid, {}).get("_disparados_real", 0)
    )
    df_lotes["total_respondeu_supabase"] = df_lotes["campanha_id"].apply(
        lambda cid: contagem.get(cid, {}).get("_respondeu", 0)
    )
    df_lotes["taxa_resposta_pct"] = df_lotes.apply(
        lambda r: (r["total_respondeu_supabase"] / r["contatos"] * 100)
        if r["contatos"] > 0 else 0,
        axis=1,
    )
    df_lotes["meta_30pct"] = df_lotes["contatos"].apply(_meta_respostas)

    # Estado pra drill-down
    if "_historico_drill_camp_id" not in st.session_state:
        st.session_state["_historico_drill_camp_id"] = None

    # Se tem drill-down ativo, renderiza ele em vez do resumo
    if st.session_state["_historico_drill_camp_id"]:
        _render_drilldown(df_lotes, contagem)
    else:
        _render_resumo_lotes(df_lotes, contagem)


# ============================================================================
# SUB-VIEW 1: RESUMO DOS LOTES
# ============================================================================

def _render_resumo_lotes(df_lotes, contagem):
    # ─── CARDS DE RESUMO ───────────────────────────────────────────────
    qtd_total = len(df_lotes)
    qtd_rodando = int((df_lotes["_status_label"] == "🤖 RODANDO").sum())
    qtd_validados = int(df_lotes["_status_label"].str.contains("VALIDADO", regex=False).sum() - 
                        df_lotes["_status_label"].str.contains("INVALIDADO", regex=False).sum())
    qtd_invalidados = int(df_lotes["_status_label"].str.contains("INVALIDADO", regex=False).sum())

    total_indicados = int(df_lotes["contatos"].sum())
    total_disparados = int(df_lotes["total_disparados_supabase"].sum())
    total_respondeu = int(df_lotes["total_respondeu_supabase"].sum())
    taxa_geral = (total_respondeu / total_indicados * 100) if total_indicados > 0 else 0

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("📦 Lotes totais", qtd_total,
        help="Total de campanhas que a Bia puxou (modo AUTO)")
    col_m2.metric("🤖 Rodando agora", qtd_rodando,
        help="Bia ainda contando respostas, sem decisão final")
    col_m3.metric("✅ Validados", qtd_validados, delta=f"-{qtd_invalidados} invalidados",
        delta_color="off",
        help="Lotes que atingiram 30% e dispararam voucher")
    col_m4.metric("📊 Taxa de resposta geral", f"{taxa_geral:.1f}%",
        help=f"{total_respondeu} / {total_indicados} indicados responderam")

    st.markdown("---")

    # ─── FILTROS ───────────────────────────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns([2, 2, 3])
    with col_f1:
        unid_filtro = st.radio(
            "Unidade:",
            ["Todas", "Mogi", "Suzano"],
            horizontal=True,
            key="hist_bia_unid",
        )
    with col_f2:
        status_opcoes = ["Todos", "🤖 Rodando", "✅ Validados", "❌ Invalidados"]
        status_filtro = st.selectbox("Status:", status_opcoes, key="hist_bia_status")
    with col_f3:
        busca = st.text_input(
            "🔍 Buscar cadastrante:",
            placeholder="Nome ou telefone",
            key="hist_bia_busca",
        )

    # Aplica filtros
    df_f = df_lotes.copy()
    if unid_filtro != "Todas":
        df_f = df_f[df_f["unidade"].str.lower() == unid_filtro.lower()]
    if status_filtro == "🤖 Rodando":
        df_f = df_f[df_f["_status_label"] == "🤖 RODANDO"]
    elif status_filtro == "✅ Validados":
        df_f = df_f[df_f["_status_label"].str.contains("✅", regex=False)]
    elif status_filtro == "❌ Invalidados":
        df_f = df_f[df_f["_status_label"].str.contains("❌", regex=False)]
    if busca.strip():
        b = busca.strip().lower()
        mask = (
            df_f["nome"].astype(str).str.lower().str.contains(b, na=False) |
            df_f["telefone"].astype(str).str.contains(b, na=False)
        )
        df_f = df_f[mask]

    # Ordena por mais recente primeiro
    df_f = df_f.sort_values("bia_puxou_em_dt", ascending=False).reset_index(drop=True)

    st.caption(f"📍 Mostrando **{len(df_f)}** de {len(df_lotes)} lotes")

    if df_f.empty:
        st.info("Nenhum lote com esses filtros.")
        return

    # ─── CSS local ─────────────────────────────────────────────────────
    st.markdown(
        """
    <style>
    .lote-card {
        padding: 14px 18px;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        margin-bottom: 10px;
        background: white;
        transition: all 0.15s ease;
    }
    .lote-card:hover { border-color: #5BC0BE; box-shadow: 0 2px 8px rgba(91,192,190,0.15); }
    .status-ok      { background: #dcfce7; color: #166534; padding: 3px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; }
    .status-alerta  { background: #fee2e2; color: #991b1b; padding: 3px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; }
    .status-info    { background: #dbeafe; color: #1e40af; padding: 3px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; }
    .progress-mini  { background: #e5e7eb; border-radius: 6px; height: 18px; overflow: hidden; margin-top: 6px; }
    .progress-mini-fill { background: linear-gradient(90deg, #5BC0BE 0%, #3D9991 100%); height: 100%; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    # ─── CARDS DOS LOTES ───────────────────────────────────────────────
    for idx, row in df_f.iterrows():
        nome = row["nome"] or "(sem nome)"
        tel = _formatar_telefone(row["telefone"])
        unid = row["unidade"] or "?"
        ind = int(row["contatos"])
        disp = int(row["total_disparados_supabase"])
        resp = int(row["total_respondeu_supabase"])
        meta = int(row["meta_30pct"])
        taxa = row["taxa_resposta_pct"]
        pct_progresso = min(100, int(resp / meta * 100)) if meta > 0 else 0

        puxou_str = _humanizar_tempo(row["bia_puxou_em_dt"])
        bia_dt = row["bia_puxou_em_dt"]
        bia_str = bia_dt.strftime("%d/%m %H:%M") if bia_dt else "—"

        # Tempo restante até timeout (se rodando)
        tempo_extra = ""
        if row["_status_label"] == "🤖 RODANDO":
            horas_rest = max(0, 36 - row["horas_rodando"])
            cor_t = "#ef4444" if horas_rest < 6 else "#6b7280"
            tempo_extra = f'<span style="color: {cor_t}; font-size: 12px; margin-left: 8px;">⏰ {horas_rest:.1f}h até timeout</span>'

        st.markdown(
            f"""
            <div class="lote-card">
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                  <strong style="font-size: 15px;">{nome}</strong>
                  <span style="color: #6b7280; font-size: 13px;"> · 📱 {tel} · 📍 {unid}</span>
                </div>
                <div>
                  <span class="status-{row['_status_class']}">{row['_status_label']}</span>
                </div>
              </div>
              <div style="margin-top: 8px; color: #6b7280; font-size: 13px;">
                🤖 Bia puxou em <strong>{bia_str}</strong> (há {puxou_str}){tempo_extra}
              </div>
              <div style="margin-top: 10px;">
                <div style="display: flex; justify-content: space-between; font-size: 12px; color: #374151;">
                  <span>📨 {disp}/{ind} disparados · 💬 {resp} responderam · meta {meta} (30%)</span>
                  <strong style="color: {'#059669' if pct_progresso >= 100 else '#5BC0BE'};">{taxa:.1f}%</strong>
                </div>
                <div class="progress-mini">
                  <div class="progress-mini-fill" style="width: {pct_progresso}%;"></div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Botão "Ver detalhes"
        col_btn, _ = st.columns([1, 4])
        with col_btn:
            if st.button(
                f"🔍 Ver os {ind} contatos",
                key=f"drill_btn_{row['campanha_id']}",
                use_container_width=True,
            ):
                st.session_state["_historico_drill_camp_id"] = row["campanha_id"]
                st.session_state["_historico_drill_nome"] = nome
                st.rerun()


# ============================================================================
# SUB-VIEW 2: DRILL-DOWN (contatos individuais do lote)
# ============================================================================

def _render_drilldown(df_lotes, contagem):
    camp_id = st.session_state["_historico_drill_camp_id"]
    nome_cad = st.session_state.get("_historico_drill_nome", "?")

    # Botão voltar
    if st.button("← Voltar pra lista de lotes", key="back_to_lotes"):
        st.session_state["_historico_drill_camp_id"] = None
        st.rerun()

    # Acha o lote no df pra mostrar header
    lote = df_lotes[df_lotes["campanha_id"] == camp_id]
    if lote.empty:
        st.error(f"Lote {camp_id} não encontrado.")
        return

    row = lote.iloc[0]

    # Header do lote
    st.markdown(f"## 🔍 Detalhes do lote — {nome_cad}")

    col_h1, col_h2, col_h3, col_h4 = st.columns(4)
    col_h1.metric("📨 Indicados", int(row["contatos"]))
    col_h2.metric("🚀 Disparados", int(row["total_disparados_supabase"]))
    col_h3.metric("💬 Responderam",
        int(row["total_respondeu_supabase"]),
        delta=f"meta: {int(row['meta_30pct'])}",
        delta_color="off")
    col_h4.metric("📊 Taxa", f"{row['taxa_resposta_pct']:.1f}%")

    bia_dt = row["bia_puxou_em_dt"]
    bia_str = bia_dt.strftime("%d/%m/%Y %H:%M") if bia_dt else "—"
    st.caption(
        f"🤖 Bia puxou em **{bia_str}** ({_humanizar_tempo(bia_dt)}) · "
        f"📍 {row['unidade'] or '?'} · "
        f"Status: **{row['_status_label']}**"
    )

    st.markdown("---")

    # Carrega disparos individuais
    with st.spinner("Carregando disparos individuais..."):
        df_disp = _carregar_disparos_de_uma_campanha(camp_id)

    if df_disp.empty:
        st.warning(
            "⚠️ Nenhum disparo encontrado no Supabase pra essa campanha.\n\n"
            "Isso pode acontecer se:\n"
            "- A Bia puxou agora mesmo e ainda não inseriu disparos\n"
            "- O Cron 6 v2 não terminou de processar o lote\n"
            "- Houve erro no nó 'Grava bia_disparos' do n8n"
        )
        return

    # ─── Resumo por status ─────────────────────────────────────────────
    st.markdown("### 📊 Disparos por status")

    if "status" in df_disp.columns:
        status_counts = df_disp["status"].fillna("DESCONHECIDO").value_counts()
        cols_status = st.columns(min(6, len(status_counts)))
        emoji_map = {
            "FILA": "⏳",
            "DISPARADO": "🚀",
            "RESPONDEU": "💬",
            "IGNOROU": "🔕",
            "SKIP_BASE": "⏭️",
            "ERRO_NUMERO_INVALIDO": "❌",
            "BLOQUEADO_PELO_INDICADO": "🚫",
        }
        for i, (status, qtd) in enumerate(status_counts.items()):
            with cols_status[i % len(cols_status)]:
                emoji = emoji_map.get(status, "❓")
                st.metric(f"{emoji} {status}", int(qtd))

    st.markdown("---")

    # ─── Tabela detalhada ──────────────────────────────────────────────
    st.markdown("### 📋 Lista completa")

    # Filtro por status
    if "status" in df_disp.columns:
        status_disponiveis = sorted(df_disp["status"].fillna("DESCONHECIDO").unique().tolist())
        status_sel = st.multiselect(
            "Filtrar por status:",
            status_disponiveis,
            default=[],
            placeholder="Todos os status",
            key="drill_filtro_status",
        )
        if status_sel:
            df_disp = df_disp[df_disp["status"].fillna("DESCONHECIDO").isin(status_sel)]

    if df_disp.empty:
        st.info("Nenhum disparo com esses filtros.")
        return

    # Monta DataFrame display
    df_display = df_disp.copy()

    # Formatar telefone
    if "telefone" in df_display.columns:
        df_display["📱 Telefone"] = df_display["telefone"].apply(_formatar_telefone)

    # Formatar timestamps
    for col_ts, col_label in [
        ("disparado_em", "🚀 Disparado em"),
        ("respondeu_em", "💬 Respondeu em"),
    ]:
        if col_ts in df_display.columns:
            df_display[col_label] = pd.to_datetime(
                df_display[col_ts], errors="coerce", utc=True
            ).dt.tz_convert(TZ_SP).dt.strftime("%d/%m %H:%M")
            df_display[col_label] = df_display[col_label].fillna("—")

    # Status com emoji
    if "status" in df_display.columns:
        df_display["🚦 Status"] = df_display["status"].fillna("DESCONHECIDO")

    # Nome do indicado (se vier do disparo) ou primeira msg
    nome_col = None
    for cand in ["nome_indicado", "nome"]:
        if cand in df_display.columns:
            nome_col = cand
            break
    if nome_col:
        df_display["👤 Indicado"] = df_display[nome_col].fillna("(sem nome)").astype(str)

    # Primeira mensagem do cliente (se tiver coluna)
    for cand in ["primeira_msg", "msg_cliente", "ultima_msg"]:
        if cand in df_display.columns:
            df_display["💬 Primeira msg"] = df_display[cand].fillna("—").astype(str).str[:80]
            break

    # Seleciona colunas pra display
    cols_ordem = [
        "👤 Indicado",
        "📱 Telefone",
        "🚦 Status",
        "🚀 Disparado em",
        "💬 Respondeu em",
        "💬 Primeira msg",
    ]
    cols_existentes = [c for c in cols_ordem if c in df_display.columns]
    df_final = df_display[cols_existentes]

    st.dataframe(df_final, use_container_width=True, hide_index=True, height=500)
    st.caption(f"📋 {len(df_final)} disparo(s) listados")
