"""
==============================================================================
ABA HISTÓRICO BIA — Lotes que a Bia v5 puxou (modo AUTO)
==============================================================================

v2.1 (22/06/2026): conserta bug do '</div>' literal aparecendo no card.
Causa: f-string multiline com indentação de 12 espaços fazia o Streamlit/
Markdown interpretar como code block e quebrar parsing de tags HTML.
Fix: HTML renderizado sem indentação (concatenação f-string inline).

v2 (22/06/2026): MANTÉM lotes finalizados/validados/invalidados no histórico.
Antes (v1) usava /?endpoint=validacao que filtrava status_rec=AGUARDANDO_VALIDACAO,
fazendo lotes sumirem após decisão. Agora usa /?endpoint=clientes (retorna TUDO
da aba CLIENTES) e filtra por BIA_PUXOU_EM IS NOT NULL — então qualquer lote
que a Bia trabalhou aparece, independente do status final.

Mostra TODOS os lotes que a Bia trabalhou (ou está trabalhando) em modo AUTO.
Cruza com `bia_disparos` no Supabase pra contar disparos/respostas em tempo real.

Sub-abas:
  📦 Lotes (resumo)      — tabela 1 linha por campanha + filtros
  🔍 Detalhes do lote    — clica num lote → vê os 20+ indicados individualmente

Status final de cada lote (derivado de STATUS DE AONDE PAROU + Voucher Liberado):
  🤖 RODANDO              — status_rec=AGUARDANDO_VALIDACAO, sem decisão ainda
  ⏰ TIMEOUT              — RODANDO há >36h (cron verificarTimeoutBIA pendente)
  ✅ AUTO_VALIDADO        — status_rec=FINALIZADO + voucher_liberado=SIM
  ❌ AUTO_INVALIDADO      — status_rec=INVALIDADO_AVISADO ou INVALIDADO_COBRADO
  🔒 ENCERRADO            — status_rec=ENCERRADO (sem segunda chance)

Fontes:
  • Apps Script Z-API /?endpoint=clientes            → TODOS lotes + status
  • Apps Script Z-API /?endpoint=contatos_cliente    → indicados do lote
  • Supabase tabela bia_disparos                     → R1/R2/respostas em tempo real

LIMITAÇÃO: Cobre só os clientes da aba CLIENTES (não inclui CLIENTES_ARQUIVO).
O cron arquivarTerminais roda dia 2 do mês — então lotes finalizados ficam
visíveis até o próximo dia 2. Pra histórico mais antigo, precisaria endpoint
novo no GAS pra ler CLIENTES_ARQUIVO.
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
    Carrega TODOS os lotes onde a Bia puxou (BIA_PUXOU_EM preenchido em CLIENTES).

    v2 (22/06/2026): TROCADO endpoint validacao → clientes.
    Antes filtrava só AGUARDANDO_VALIDACAO; agora retorna TODOS os status,
    incluindo FINALIZADO, INVALIDADO_AVISADO, INVALIDADO_COBRADO e ENCERRADO.

    Cobertura: aba CLIENTES (não inclui CLIENTES_ARQUIVO ainda).
    """
    data = _zapi_get("clientes")
    if isinstance(data, dict) and data.get("_erro"):
        return pd.DataFrame(), data.get("_erro")

    linhas = data.get("linhas", [])
    if not linhas:
        return pd.DataFrame(), None

    df = pd.DataFrame(linhas)

    # Filtra SÓ os que a Bia puxou (BIA_PUXOU_EM preenchido)
    if "BIA_PUXOU_EM" not in df.columns:
        return pd.DataFrame(), (
            "Coluna BIA_PUXOU_EM não retornada pelo Apps Script — "
            "rodar migrarV98() no editor do GAS pra adicionar a coluna."
        )

    df["bia_puxou_em_dt"] = df["BIA_PUXOU_EM"].apply(_parse_iso)
    df = df[df["bia_puxou_em_dt"].notna()].copy()

    if df.empty:
        return pd.DataFrame(), None

    # Renomeia colunas do endpoint clientes pra match com nomes esperados pelo resto
    df = df.rename(columns={
        "Telefone": "telefone",
        "Nome": "nome",
        "Unidade": "unidade",
        "Funcionaria": "funcionaria",
        "ID Campanha": "campanha_id",
        "Total Indicacoes": "contatos",
        "Voucher Liberado": "voucher_liberado",
        "PRIVACIDADE": "privacidade",
        "STATUS DE AONDE PAROU": "status_rec",
        "DATA BATEU META": "data_bateu_meta",
    })

    # Garante tipos
    df["contatos"] = pd.to_numeric(df.get("contatos", 0), errors="coerce").fillna(0).astype(int)
    df["status_rec"] = df.get("status_rec", "").astype(str).str.strip()
    df["voucher_liberado"] = df.get("voucher_liberado", "").astype(str).str.upper().str.strip()
    df["unidade"] = df.get("unidade", "").astype(str)
    df["nome"] = df.get("nome", "").fillna("").astype(str)
    df["telefone"] = df.get("telefone", "").astype(str)
    df["campanha_id"] = df.get("campanha_id", "").astype(str)

    # Limpa sufixos _COBRADO1 / _COBRADO2 pra simplificar derivação
    df["status_rec_base"] = (
        df["status_rec"]
        .str.replace("_COBRADO2", "", regex=False)
        .str.replace("_COBRADO1", "", regex=False)
    )

    # Status derivado pra cada lote
    agora = datetime.now(TZ_SP)

    def _status_final(row):
        status = row["status_rec_base"]
        voucher = row["voucher_liberado"]

        # 1) VALIDADO: voucher enviado OU status final FINALIZADO
        if status == "FINALIZADO" or voucher == "SIM":
            return ("✅ AUTO_VALIDADO", "ok")

        # 2) INVALIDADO: foi reprovado (1ª ou 2ª vez)
        if status in ("INVALIDADO_AVISADO", "INVALIDADO_COBRADO"):
            return ("❌ AUTO_INVALIDADO", "alerta")

        # 3) ENCERRADO: foi reprovado 2x ou sem resposta
        if status == "ENCERRADO":
            return ("🔒 ENCERRADO", "neutro")
        if status == "_COBRADOSEMRESPOSTA":
            return ("🔇 SEM RESPOSTA", "neutro")

        # 4) RODANDO: ainda em AGUARDANDO_VALIDACAO
        if status == "AGUARDANDO_VALIDACAO":
            horas = (agora - row["bia_puxou_em_dt"]).total_seconds() / 3600
            if horas >= 36:
                return ("⏰ TIMEOUT (aguardando cron)", "alerta")
            return ("🤖 RODANDO", "info")

        # 5) Fallback
        return (f"❓ {status or '(sem status)'}", "neutro")

    df[["_status_label", "_status_class"]] = df.apply(
        lambda r: pd.Series(_status_final(r)), axis=1
    )

    # Tempo decorrido desde Bia puxar
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
        STATUS_EFETIVAMENTE_ENVIADO = {"DISPARADO", "RESPONDEU", "IGNOROU",
                                        "ERRO_NUMERO_INVALIDO", "BLOQUEADO_PELO_INDICADO"}
        contagem = {}
        for row in result.data or []:
            cid = row.get("campanha_id")
            if not cid:
                continue
            if cid not in contagem:
                contagem[cid] = {"_total": 0, "_respondeu": 0, "_disparados_real": 0, "_skip_base": 0}
            status = (row.get("status") or "DESCONHECIDO").upper()
            contagem[cid][status] = contagem[cid].get(status, 0) + 1
            contagem[cid]["_total"] += 1
            if status in STATUS_EFETIVAMENTE_ENVIADO:
                contagem[cid]["_disparados_real"] += 1
            if status == "SKIP_BASE":
                contagem[cid]["_skip_base"] += 1
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
        "Inclui campanhas **rodando, validadas e invalidadas** — "
        "histórico completo até o arquivamento mensal (dia 2)."
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
    df_lotes["total_skip_base"] = df_lotes["campanha_id"].apply(
        lambda cid: contagem.get(cid, {}).get("_skip_base", 0)
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
    # v2: contagem baseada em _status_class (mais confiável que substring match)
    qtd_total = len(df_lotes)
    qtd_rodando = int((df_lotes["_status_class"] == "info").sum())
    qtd_validados = int(df_lotes["_status_label"].str.startswith("✅").sum())
    qtd_invalidados = int(df_lotes["_status_label"].str.startswith("❌").sum())
    qtd_encerrados = int(
        df_lotes["_status_label"].str.startswith("🔒").sum() +
        df_lotes["_status_label"].str.startswith("🔇").sum()
    )

    total_indicados = int(df_lotes["contatos"].sum())
    total_respondeu = int(df_lotes["total_respondeu_supabase"].sum())
    taxa_geral = (total_respondeu / total_indicados * 100) if total_indicados > 0 else 0

    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    col_m1.metric("📦 Lotes totais", qtd_total,
        help="Total de campanhas que a Bia puxou (modo AUTO), incluindo validadas/invalidadas")
    col_m2.metric("🤖 Rodando agora", qtd_rodando,
        help="Bia ainda contando respostas, sem decisão final")
    col_m3.metric("✅ Validados", qtd_validados,
        help="Lotes que atingiram 30% e dispararam voucher")
    col_m4.metric("❌ Invalidados", qtd_invalidados,
        delta=f"+{qtd_encerrados} encerrados" if qtd_encerrados > 0 else None,
        delta_color="off",
        help="Lotes que não atingiram 30% no prazo (e os encerrados em segunda chance)")
    col_m5.metric("📊 Taxa de resposta", f"{taxa_geral:.1f}%",
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
        # v2: opções expandidas pra incluir encerrados e finalizados
        status_opcoes = [
            "Todos",
            "🟢 Ativos (rodando + timeout)",
            "🤖 Rodando",
            "⏰ Timeout",
            "✅ Validados",
            "❌ Invalidados",
            "🔒 Encerrados / Sem resposta",
        ]
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

    if status_filtro == "🟢 Ativos (rodando + timeout)":
        df_f = df_f[df_f["status_rec_base"] == "AGUARDANDO_VALIDACAO"]
    elif status_filtro == "🤖 Rodando":
        df_f = df_f[df_f["_status_label"] == "🤖 RODANDO"]
    elif status_filtro == "⏰ Timeout":
        df_f = df_f[df_f["_status_label"].str.startswith("⏰")]
    elif status_filtro == "✅ Validados":
        df_f = df_f[df_f["_status_label"].str.startswith("✅")]
    elif status_filtro == "❌ Invalidados":
        df_f = df_f[df_f["_status_label"].str.startswith("❌")]
    elif status_filtro == "🔒 Encerrados / Sem resposta":
        df_f = df_f[
            df_f["_status_label"].str.startswith("🔒") |
            df_f["_status_label"].str.startswith("🔇")
        ]

    if busca.strip():
        b = busca.strip().lower()
        mask = (
            df_f["nome"].astype(str).str.lower().str.contains(b, na=False) |
            df_f["telefone"].astype(str).str.contains(b, na=False)
        )
        df_f = df_f[mask]

    # Ordena: ativos no topo, depois pela ordem temporal (mais recente primeiro)
    df_f = df_f.copy()
    df_f["_ordem_status"] = df_f["_status_class"].map({
        "info": 0,      # rodando
        "alerta": 1,    # timeout, invalidado
        "ok": 2,        # validado
        "neutro": 3,    # encerrado, sem resposta
    }).fillna(4)
    df_f = df_f.sort_values(
        ["_ordem_status", "bia_puxou_em_dt"],
        ascending=[True, False]
    ).reset_index(drop=True)

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
    .lote-card-finalizado { background: #fafafa; opacity: 0.92; }
    .status-ok      { background: #dcfce7; color: #166534; padding: 3px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; }
    .status-alerta  { background: #fee2e2; color: #991b1b; padding: 3px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; }
    .status-info    { background: #dbeafe; color: #1e40af; padding: 3px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; }
    .status-neutro  { background: #f3f4f6; color: #4b5563; padding: 3px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; }
    .badge-voucher  { background: #fef3c7; color: #92400e; padding: 2px 8px; border-radius: 10px; font-weight: 600; font-size: 10px; margin-left: 6px; }
    .progress-mini  { background: #e5e7eb; border-radius: 6px; height: 18px; overflow: hidden; margin-top: 6px; }
    .progress-mini-fill { background: linear-gradient(90deg, #5BC0BE 0%, #3D9991 100%); height: 100%; }
    .progress-mini-fill-final { background: linear-gradient(90deg, #94a3b8 0%, #64748b 100%); height: 100%; }
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
        skip = int(row["total_skip_base"])
        resp = int(row["total_respondeu_supabase"])
        meta = int(row["meta_30pct"])
        taxa = row["taxa_resposta_pct"]
        pct_progresso = min(100, int(resp / meta * 100)) if meta > 0 else 0

        puxou_str = _humanizar_tempo(row["bia_puxou_em_dt"])
        bia_dt = row["bia_puxou_em_dt"]
        bia_str = bia_dt.strftime("%d/%m %H:%M") if bia_dt else "—"

        # v2: badge extra "🎁 Voucher" pra validados
        badge_voucher = ""
        if row["voucher_liberado"] == "SIM":
            badge_voucher = '<span class="badge-voucher">🎁 Voucher enviado</span>'

        # Tempo restante até timeout (se rodando) ou info de finalizado
        tempo_extra = ""
        is_finalizado = row["_status_class"] in ("ok", "alerta", "neutro") and \
                        row["status_rec_base"] != "AGUARDANDO_VALIDACAO"

        if row["_status_label"] == "🤖 RODANDO":
            horas_rest = max(0, 36 - row["horas_rodando"])
            cor_t = "#ef4444" if horas_rest < 6 else "#6b7280"
            tempo_extra = f'<span style="color: {cor_t}; font-size: 12px; margin-left: 8px;">⏰ {horas_rest:.1f}h até timeout</span>'

        # CSS: cards finalizados ficam mais discretos
        card_class = "lote-card lote-card-finalizado" if is_finalizado else "lote-card"
        progress_class = "progress-mini-fill-final" if is_finalizado else "progress-mini-fill"

        # v2.1: HTML sem indentação (Streamlit/Markdown trata 4+ espaços como
        # code block e quebra parsing de tags HTML inline). Tudo numa string
        # contínua, sem newlines/indents internos.
        cor_taxa = '#059669' if pct_progresso >= 100 else '#5BC0BE'
        html_card = (
            f'<div class="{card_class}">'
            f'<div style="display: flex; justify-content: space-between; align-items: center;">'
            f'<div>'
            f'<strong style="font-size: 15px;">{nome}</strong>'
            f'<span style="color: #6b7280; font-size: 13px;"> · 📱 {tel} · 📍 {unid}</span>'
            f'</div>'
            f'<div>'
            f'<span class="status-{row["_status_class"]}">{row["_status_label"]}</span>'
            f'{badge_voucher}'
            f'</div>'
            f'</div>'
            f'<div style="margin-top: 8px; color: #6b7280; font-size: 13px;">'
            f'🤖 Bia puxou em <strong>{bia_str}</strong> (há {puxou_str}){tempo_extra}'
            f'</div>'
            f'<div style="margin-top: 10px;">'
            f'<div style="display: flex; justify-content: space-between; font-size: 12px; color: #374151;">'
            f'<span>📨 {disp}/{ind} enviados · 🛡️ {skip} skip · 💬 {resp} responderam · meta {meta} (30%)</span>'
            f'<strong style="color: {cor_taxa};">{taxa:.1f}%</strong>'
            f'</div>'
            f'<div class="progress-mini">'
            f'<div class="{progress_class}" style="width: {pct_progresso}%;"></div>'
            f'</div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(html_card, unsafe_allow_html=True)

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

    # v2: mostra também voucher_liberado se SIM
    extra_status = ""
    if row.get("voucher_liberado") == "SIM":
        extra_status = " · 🎁 **Voucher enviado**"

    st.caption(
        f"🤖 Bia puxou em **{bia_str}** ({_humanizar_tempo(bia_dt)}) · "
        f"📍 {row['unidade'] or '?'} · "
        f"Status: **{row['_status_label']}**{extra_status}"
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

    # 🔔 Último Lembrete (R1 ou R2) — baseado em tentativas_envio + ultima_notif_recepcao
    if "tentativas_envio" in df_display.columns and "ultima_notif_recepcao" in df_display.columns:
        notif_fmt = pd.to_datetime(
            df_display["ultima_notif_recepcao"], errors="coerce", utc=True
        ).dt.tz_convert(TZ_SP).dt.strftime("%d/%m %H:%M")

        def _format_lembrete(row, hora):
            tent = row.get("tentativas_envio") or 0
            try:
                tent = int(tent)
            except (ValueError, TypeError):
                tent = 0
            if tent <= 1 or not hora or pd.isna(hora):
                return "—"
            if tent == 2:
                return f"🔔 R1 · {hora}"
            if tent == 3:
                return f"🔔 R2 · {hora}"
            return f"🔔 R{tent - 1} · {hora}"

        df_display["🔔 Último Lembrete"] = [
            _format_lembrete(df_display.iloc[i], notif_fmt.iloc[i])
            for i in range(len(df_display))
        ]

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
        "🔔 Último Lembrete",
        "💬 Respondeu em",
        "💬 Primeira msg",
    ]
    cols_existentes = [c for c in cols_ordem if c in df_display.columns]
    df_final = df_display[cols_existentes]

    st.dataframe(df_final, use_container_width=True, hide_index=True, height=500)
    st.caption(f"📋 {len(df_final)} disparo(s) listados")
