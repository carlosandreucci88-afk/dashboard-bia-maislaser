"""
==============================================================================
ROBÔ PÓS-ATENDIMENTO — Abas "Histórico de disparos" + "Monitoramento clientes"
==============================================================================
v1.0 (04/07/2026)
v1.1 (04/07/2026): Filtro personalizado de data da sessão no Monitoramento

Parte B — render_aba_pos_historico():
    Lista de disparos passados. Métricas agregadas + drill-down.

Parte C — render_aba_pos_monitor():
    Cards de saúde (NPS, cupons, respostas)
    Lista filtrável de clientes (status, unidade, data da sessão, busca)
    Drill-down: ver histórico de interações do cliente
==============================================================================
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))

STATUS_LABEL = {
    "aguardando_disparo":        "🟡 Aguardando disparo",
    "template_enviado":          "📤 Template enviado",
    "tudo_otimo_pendente":       "🟢 Tudo ótimo (esperando cupom)",
    "tudo_otimo_cupom_agora":    "🎁 Satisfeito + cupom agora",
    "tudo_otimo_cupom_depois":   "✅ Satisfeito + cupom depois",
    "cupom_agora_direto":        "🎁 Cupom (via texto livre)",
    "problema_atendimento":      "🔴 Problema atendimento",
    "resultado_ruim":            "🔴 Resultado ruim",
    "redirecionado_coordenadora":"⚠️ Redirecionado (inválidas)",
    "sem_resposta_24h":          "⚪ Sem resposta (24h)",
    "duplicata_ignorada":        "⏭️ Duplicata ignorada",
    "falha_envio":               "❌ Falha no envio",
    "substituido_por_novo_disparo": "🔄 Substituído por novo disparo",
    "expirado_24h":                  "⏰ Expirado (>24h sem interação)",
}


# ============================================================================
# CONEXÃO
# ============================================================================
@st.cache_resource
def _get_sb() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=30, show_spinner=False)
def _get_stats_pos(dias: int = 30) -> dict:
    """Chama RPC pos_get_stats."""
    try:
        sb = _get_sb()
        r = sb.rpc("pos_get_stats", {"dias": dias}).execute()
        if isinstance(r.data, dict):
            return r.data
    except Exception as e:
        st.error(f"⚠️ Erro RPC pos_get_stats: {e}")
    return {}


@st.cache_data(ttl=30, show_spinner=False)
def _get_historico_disparos(limit: int = 100) -> pd.DataFrame:
    try:
        sb = _get_sb()
        r = (sb.table("pos_atendimento_disparos_historico")
               .select("*").order("criado_em", desc=True).limit(limit).execute())
        return pd.DataFrame(r.data or [])
    except Exception as e:
        st.error(f"⚠️ Erro ao ler histórico: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def _get_clientes(limit: int = 2000) -> pd.DataFrame:
    try:
        sb = _get_sb()
        r = (sb.table("pos_atendimento_clientes")
               .select("*").order("criado_em", desc=True).limit(limit).execute())
        return pd.DataFrame(r.data or [])
    except Exception as e:
        st.error(f"⚠️ Erro ao ler clientes: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def _get_log_cliente(cliente_id: int) -> pd.DataFrame:
    try:
        sb = _get_sb()
        r = (sb.table("pos_atendimento_log")
               .select("*").eq("cliente_id", cliente_id)
               .order("data_hora", desc=False).execute())
        return pd.DataFrame(r.data or [])
    except Exception:
        return pd.DataFrame()


# ============================================================================
# HELPERS
# ============================================================================

def _fmt_dt(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = pd.to_datetime(iso_str, utc=True).tz_convert(TZ_SP)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return str(iso_str)[:16].replace("T", " ")


def _fmt_data(v) -> str:
    if not v:
        return "—"
    try:
        return pd.to_datetime(v).strftime("%d/%m/%Y")
    except Exception:
        return str(v)[:10]


def _card(icon: str, valor, label: str, cor: str = "#5BC0BE", sub: str = None) -> str:
    sub_html = f'<div style="font-size:11px;color:#9CA3AF;margin-top:2px;">{sub}</div>' if sub else ''
    return (
        f'<div style="background:white;border-radius:12px;padding:16px;border:1px solid #E5E7EB;box-shadow:0 1px 2px rgba(0,0,0,0.03);">'
        f'<div style="display:flex;align-items:center;gap:12px;">'
        f'<div style="width:40px;height:40px;border-radius:10px;background:{cor}1A;color:{cor};display:flex;align-items:center;justify-content:center;font-size:22px;">{icon}</div>'
        f'<div>'
        f'<div style="font-size:24px;font-weight:700;color:#111827;">{valor}</div>'
        f'<div style="font-size:12px;color:#6B7280;text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
        f'{sub_html}'
        f'</div></div></div>'
    )


# ============================================================================
# PARTE B — HISTÓRICO DE DISPAROS
# ============================================================================

def render_aba_pos_historico():
    st.markdown("## 📋 Histórico de disparos")
    st.caption("Cada linha é um upload de planilha processado. Registra sucessos, erros e detalhes de cada envio.")

    # ═════════════════════════════════════════════════════════════════
    # FILTROS
    # ═════════════════════════════════════════════════════════════════

    # ── Linha 1: Filtro por UNIDADE ──
    if "pos_hist_unidade" not in st.session_state:
        st.session_state.pos_hist_unidade = "Todas"

    col_u1, col_u2, col_u3, col_ln = st.columns([1.2, 1.2, 1.2, 1.4])

    with col_u1:
        ativo = st.session_state.pos_hist_unidade == "Todas"
        if st.button("🏢 Todas",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_todas"):
            st.session_state.pos_hist_unidade = "Todas"
            st.rerun()

    with col_u2:
        ativo = st.session_state.pos_hist_unidade == "Mogi"
        if st.button("📍 Mogi",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_mogi"):
            st.session_state.pos_hist_unidade = "Mogi"
            st.rerun()

    with col_u3:
        ativo = st.session_state.pos_hist_unidade == "Suzano"
        if st.button("📍 Suzano",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_suzano"):
            st.session_state.pos_hist_unidade = "Suzano"
            st.rerun()

    with col_ln:
        limit = st.selectbox("Últimos", [30, 100, 300, 1000], index=1, key="pos_hist_limit")

    # ── Linha 2: Filtro por PERÍODO ──
    if "pos_hist_periodo" not in st.session_state:
        st.session_state.pos_hist_periodo = "Tudo"

    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns([1, 1.2, 1.2, 1, 1.6])

    with col_p1:
        ativo = st.session_state.pos_hist_periodo == "Hoje"
        if st.button("📆 Hoje",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_hoje"):
            st.session_state.pos_hist_periodo = "Hoje"
            st.rerun()

    with col_p2:
        ativo = st.session_state.pos_hist_periodo == "7dias"
        if st.button("🕐 Últimos 7 dias",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_7d"):
            st.session_state.pos_hist_periodo = "7dias"
            st.rerun()

    with col_p3:
        ativo = st.session_state.pos_hist_periodo == "30dias"
        if st.button("📅 Últimos 30 dias",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_30d"):
            st.session_state.pos_hist_periodo = "30dias"
            st.rerun()

    with col_p4:
        ativo = st.session_state.pos_hist_periodo == "Tudo"
        if st.button("♾️ Tudo",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_tudo"):
            st.session_state.pos_hist_periodo = "Tudo"
            st.rerun()

    with col_p5:
        ativo = st.session_state.pos_hist_periodo == "Personalizado"
        if st.button("📅 Personalizado",
                     type="primary" if ativo else "secondary",
                     use_container_width=True, key="pos_hist_btn_pers"):
            st.session_state.pos_hist_periodo = "Personalizado"
            st.rerun()

    # ── Date range quando "Personalizado" ──
    data_de = data_ate = None
    if st.session_state.pos_hist_periodo == "Personalizado":
        agora_date = datetime.now(TZ_SP).date()
        if "pos_hist_data_de" not in st.session_state:
            st.session_state.pos_hist_data_de = agora_date.replace(day=1)
        if "pos_hist_data_ate" not in st.session_state:
            st.session_state.pos_hist_data_ate = agora_date

        col_d1, col_d2, _ = st.columns([1.5, 1.5, 3])
        with col_d1:
            data_de = st.date_input("De:", value=st.session_state.pos_hist_data_de,
                                    key="pos_hist_dpicker_de", format="DD/MM/YYYY")
            st.session_state.pos_hist_data_de = data_de
        with col_d2:
            data_ate = st.date_input("Até:", value=st.session_state.pos_hist_data_ate,
                                     key="pos_hist_dpicker_ate", format="DD/MM/YYYY")
            st.session_state.pos_hist_data_ate = data_ate

        if data_de and data_ate and data_de > data_ate:
            st.warning("⚠️ Data inicial é depois da final. Inverta as datas.")

    # ═════════════════════════════════════════════════════════════════
    # BUSCA DE DADOS
    # ═════════════════════════════════════════════════════════════════
    df = _get_historico_disparos(limit=limit)

    if df.empty:
        st.info("Nenhum disparo registrado ainda. Faça um upload na aba 🚀 Disparar Pós-atendimento.")
        return

    # ── Aplica filtros ──
    df["criado_em_ts"] = pd.to_datetime(df["criado_em"], utc=True).dt.tz_convert(TZ_SP)

    # Filtro unidade
    if st.session_state.pos_hist_unidade == "Mogi":
        df = df[df["unidade"].astype(str).str.contains("Mogi", case=False, na=False)]
    elif st.session_state.pos_hist_unidade == "Suzano":
        df = df[df["unidade"].astype(str).str.contains("Suzano", case=False, na=False)]

    # Filtro período
    agora = datetime.now(TZ_SP)
    periodo = st.session_state.pos_hist_periodo
    if periodo == "Hoje":
        inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        df = df[df["criado_em_ts"] >= inicio_hoje]
    elif periodo == "7dias":
        df = df[df["criado_em_ts"] >= agora - timedelta(days=7)]
    elif periodo == "30dias":
        df = df[df["criado_em_ts"] >= agora - timedelta(days=30)]
    elif periodo == "Personalizado" and data_de and data_ate and data_de <= data_ate:
        dt_de = datetime.combine(data_de, datetime.min.time()).replace(tzinfo=TZ_SP)
        dt_ate = datetime.combine(data_ate, datetime.max.time()).replace(tzinfo=TZ_SP)
        df = df[(df["criado_em_ts"] >= dt_de) & (df["criado_em_ts"] <= dt_ate)]

    if df.empty:
        st.info("Nenhum disparo nos filtros selecionados. Ajuste unidade ou período.")
        return

    # ── Métricas agregadas ──
    total_disparos = len(df)
    total_clientes = int(df["template_enviados_ok"].fillna(0).sum())
    total_erros = int(df["erros_envio"].fillna(0).sum())

    col1, col2, col3, col4 = st.columns(4)
    col1.markdown(_card("📤", total_disparos, "Disparos", "#5BC0BE"), unsafe_allow_html=True)
    col2.markdown(_card("✅", total_clientes, "Templates OK", "#22c55e"), unsafe_allow_html=True)
    col3.markdown(_card("❌", total_erros, "Erros de envio", "#ef4444" if total_erros > 0 else "#9ca3af"), unsafe_allow_html=True)
    tx_sucesso = 100 * total_clientes / (total_clientes + total_erros) if (total_clientes + total_erros) > 0 else 0
    col4.markdown(_card("📊", f"{tx_sucesso:.1f}%", "Taxa sucesso", "#3b82f6"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.divider()

    # ── Tabela ──
    df_show = df.copy()
    df_show["criado_em_fmt"] = df_show["criado_em"].apply(_fmt_dt)
    df_show["arquivo"] = df_show["arquivo"].fillna("—")

    for _, row in df_show.iterrows():
        with st.expander(
            f"📅 **{row['criado_em_fmt']}** · {row['unidade'].replace('Mogi das Cruzes', 'Mogi')} · "
            f"{row.get('template_enviados_ok', 0)} enviados / {row.get('erros_envio', 0)} erros · "
            f"arquivo: `{row['arquivo']}`"
        ):
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Linhas planilha", row.get("total_linhas_planilha", 0))
            col_a.metric("Clientes únicos", row.get("total_clientes_unicos", 0))
            col_b.metric("Templates OK ✅", row.get("template_enviados_ok", 0))
            col_b.metric("Erros ❌", row.get("erros_envio", 0))
            col_c.metric("Duplicatas", row.get("duplicatas_ignoradas", 0))
            col_c.metric("Fase", row.get("fase", "?"))

            if row.get("data_sessoes"):
                st.caption(f"📅 Datas de sessões: {row['data_sessoes']}")

            if row.get("erros_envio_detalhes"):
                st.markdown("**❌ Detalhes dos erros:**")
                st.code(row["erros_envio_detalhes"])


# ============================================================================
# PARTE C — MONITORAMENTO CLIENTES
# ============================================================================

def render_aba_pos_monitor():
    st.markdown("## 👥 Monitoramento de clientes")
    st.caption("Todos os clientes que passaram pelo robô pós-atendimento. Estado atual e histórico completo de interações.")

    stats = _get_stats_pos(30)

    # ── Cards de saúde (baseados na RPC) ──
    if stats:
        col1, col2, col3, col4 = st.columns(4)
        total_c = stats.get("total_clientes", 0)
        nps_pos = stats.get("nps_positivo", 0)
        nps_neg = stats.get("nps_negativo", 0)
        cupons = stats.get("cupons_solicitados", 0)
        taxa = stats.get("taxa_resposta")

        col1.markdown(_card("👥", total_c, "Total 30d", "#5BC0BE"), unsafe_allow_html=True)
        col2.markdown(_card("💚", nps_pos, "NPS positivo", "#22c55e",
                            sub="tudo ótimo / satisfeitos"), unsafe_allow_html=True)
        col3.markdown(_card("🔴", nps_neg, "NPS negativo", "#ef4444" if nps_neg > 0 else "#9ca3af",
                            sub="problema / ruim"), unsafe_allow_html=True)
        col4.markdown(_card("🎁", cupons, "Cupons pedidos", "#a855f7"), unsafe_allow_html=True)

        if taxa is not None:
            st.caption(f"📊 Taxa de resposta 30d: **{taxa}%**")

        st.markdown("<br>", unsafe_allow_html=True)

    st.divider()

    # ── Lista de clientes ──
    df = _get_clientes(2000)
    if df.empty:
        st.info("Nenhum cliente registrado ainda.")
        return

    # ── v1.1: Filtro personalizado por DATA DA SESSÃO ──
    st.markdown("### 🔍 Filtros")

    # Toggle liga/desliga o filtro de data
    if "pos_mon_usar_data" not in st.session_state:
        st.session_state.pos_mon_usar_data = False

    usar_data = st.checkbox(
        "📅 Filtrar por data da sessão",
        value=st.session_state.pos_mon_usar_data,
        key="pos_mon_chk_data",
        help="Ativa pra filtrar clientes por período específico da sessão realizada."
    )
    st.session_state.pos_mon_usar_data = usar_data

    data_de = data_ate = None
    if usar_data:
        agora_date = datetime.now(TZ_SP).date()
        if "pos_mon_data_de" not in st.session_state:
            st.session_state.pos_mon_data_de = agora_date.replace(day=1)
        if "pos_mon_data_ate" not in st.session_state:
            st.session_state.pos_mon_data_ate = agora_date

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            data_de = st.date_input("De:", value=st.session_state.pos_mon_data_de,
                                    key="pos_mon_dpicker_de", format="DD/MM/YYYY")
            st.session_state.pos_mon_data_de = data_de
        with col_d2:
            data_ate = st.date_input("Até:", value=st.session_state.pos_mon_data_ate,
                                     key="pos_mon_dpicker_ate", format="DD/MM/YYYY")
            st.session_state.pos_mon_data_ate = data_ate

        if data_de and data_ate and data_de > data_ate:
            st.warning("⚠️ Data inicial é depois da final. Inverta as datas.")

    # ── Filtros originais (status / unidade / busca) ──
    col_f1, col_f2, col_f3 = st.columns([2, 2, 3])

    with col_f1:
        opcoes_status = ["Todos"] + sorted(df["status"].dropna().unique().tolist())
        filtro_status = st.selectbox("Status", opcoes_status, key="pos_mon_status")

    with col_f2:
        opcoes_unidade = ["Todas"] + sorted(df["unidade"].dropna().unique().tolist())
        filtro_unidade = st.selectbox("Unidade", opcoes_unidade, key="pos_mon_unidade")

    with col_f3:
        busca = st.text_input("🔎 Buscar por nome ou telefone", key="pos_mon_busca")

    df_f = df.copy()
    if filtro_status != "Todos":
        df_f = df_f[df_f["status"] == filtro_status]
    if filtro_unidade != "Todas":
        df_f = df_f[df_f["unidade"] == filtro_unidade]
    if busca:
        bl = busca.lower()
        df_f = df_f[
            df_f["nome"].astype(str).str.lower().str.contains(bl, na=False)
            | df_f["telefone"].astype(str).str.contains(busca, na=False)
        ]

    # v1.1: aplica filtro de data da sessão (se ativo e datas válidas)
    if usar_data and data_de and data_ate and data_de <= data_ate:
        # data_sessao vem como string "YYYY-MM-DD" ou date do Postgres — normaliza pra date
        df_f = df_f.copy()
        df_f["_data_sessao_dt"] = pd.to_datetime(df_f["data_sessao"], errors="coerce").dt.date
        df_f = df_f[
            (df_f["_data_sessao_dt"] >= data_de) &
            (df_f["_data_sessao_dt"] <= data_ate)
        ]
        # Info visual do filtro ativo
        st.info(
            f"📅 Filtro de sessão ativo: **{data_de.strftime('%d/%m/%Y')}** a "
            f"**{data_ate.strftime('%d/%m/%Y')}** — {len(df_f)} cliente(s) no período."
        )

    if df_f.empty:
        st.info("Nenhum cliente nos filtros selecionados.")
        return

    # ══════════════════════════════════════════════════════════════════
    # v1.2 — PAGINAÇÃO
    # ══════════════════════════════════════════════════════════════════
    total_clientes_filtrado = len(df_f)

    st.markdown(f"### {total_clientes_filtrado} cliente(s)")

    # Hash dos filtros ativos — se mudar, reseta pra página 1
    filtros_hash = f"{filtro_status}|{filtro_unidade}|{busca}|{usar_data}|{data_de}|{data_ate}"
    if st.session_state.get("pos_mon_filtros_hash") != filtros_hash:
        st.session_state.pos_mon_pag_atual = 1
        st.session_state.pos_mon_filtros_hash = filtros_hash

    # Controles de paginação
    col_tam, col_prev, col_pag, col_next, col_info = st.columns([1.5, 1, 1.5, 1, 3])

    with col_tam:
        tam_pag = st.selectbox(
            "Por página",
            [25, 50, 100, 200],
            index=1,
            key="pos_mon_tam_pag"
        )

    total_paginas = max(1, (total_clientes_filtrado + tam_pag - 1) // tam_pag)

    if "pos_mon_pag_atual" not in st.session_state:
        st.session_state.pos_mon_pag_atual = 1
    # Garante que a página atual não excede o total (caso tam_pag mude)
    if st.session_state.pos_mon_pag_atual > total_paginas:
        st.session_state.pos_mon_pag_atual = total_paginas

    with col_prev:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        if st.button("◀ Anterior", use_container_width=True, key="pos_mon_btn_prev",
                     disabled=(st.session_state.pos_mon_pag_atual <= 1)):
            st.session_state.pos_mon_pag_atual -= 1
            st.rerun()

    with col_pag:
        pag_input = st.number_input(
            f"Página (1-{total_paginas})",
            min_value=1,
            max_value=total_paginas,
            value=st.session_state.pos_mon_pag_atual,
            step=1,
            key="pos_mon_pag_input"
        )
        if pag_input != st.session_state.pos_mon_pag_atual:
            st.session_state.pos_mon_pag_atual = pag_input
            st.rerun()

    with col_next:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        if st.button("Próxima ▶", use_container_width=True, key="pos_mon_btn_next",
                     disabled=(st.session_state.pos_mon_pag_atual >= total_paginas)):
            st.session_state.pos_mon_pag_atual += 1
            st.rerun()

    # Fatia o df pela página atual
    inicio = (st.session_state.pos_mon_pag_atual - 1) * tam_pag
    fim = inicio + tam_pag
    df_pag = df_f.iloc[inicio:fim]

    with col_info:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        st.caption(
            f"Exibindo **{inicio + 1}–{min(fim, total_clientes_filtrado)}** "
            f"de **{total_clientes_filtrado}** · Página **{st.session_state.pos_mon_pag_atual}/{total_paginas}**"
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Tabela com drill-down (só página atual) ──
    for _, row in df_pag.iterrows():
        status_label = STATUS_LABEL.get(row["status"], row["status"])
        unidade_short = str(row.get("unidade", "?")).replace("Mogi das Cruzes", "Mogi")
        data_ses = _fmt_data(row.get("data_sessao"))

        # Alerta visual pra estados problemáticos
        prefix = ""
        if row["status"] in ("problema_atendimento", "resultado_ruim", "redirecionado_coordenadora"):
            prefix = "🚨 "
        elif row["status"] == "falha_envio":
            prefix = "⚠️ "

        titulo = (
            f"{prefix}**{row['nome']}** · {unidade_short} · "
            f"sessão {data_ses} {row.get('hora_sessao') or ''} · "
            f"{status_label}"
        )

        with st.expander(titulo):
            col_a, col_b = st.columns([2, 3])

            with col_a:
                st.markdown(f"**Telefone:** +{row['telefone']}")
                st.markdown(f"**Nome completo:** {row.get('nome_completo', '—')}")
                st.markdown(f"**Profissional:** {row.get('profissional', '—')}")
                st.markdown(f"**Áreas:** {row.get('areas', '—')}")
                st.markdown(f"**Cadastrado:** {_fmt_dt(row.get('criado_em'))}")
                st.markdown(f"**Última atualização:** {_fmt_dt(row.get('ultima_atualizacao'))}")
                st.markdown(f"**Tentativas inválidas:** {row.get('tentativas_invalidas', 0)}")

                wa_link = f"https://wa.me/{row['telefone']}"
                st.markdown(f"💬 [Abrir conversa no WhatsApp]({wa_link})")

            with col_b:
                st.markdown("**📜 Histórico de interações**")
                df_log = _get_log_cliente(row["id"])
                if df_log.empty:
                    st.caption("Sem interações registradas.")
                else:
                    for _, l in df_log.iterrows():
                        hora = _fmt_dt(l["data_hora"])
                        tipo = l.get("tipo_mensagem", "?")
                        obs = l.get("observacao", "") or ""
                        conteudo = l.get("conteudo", "") or ""

                        # Cor da caixa por tipo
                        if tipo.startswith("saida"):
                            cor = "#dbeafe"
                            emoji = "🤖"
                        elif tipo.startswith("entrada"):
                            cor = "#f3f4f6"
                            emoji = "👤"
                        elif tipo == "erro_envio":
                            cor = "#fee2e2"
                            emoji = "❌"
                        else:
                            cor = "#f9fafb"
                            emoji = "ℹ️"

                        st.markdown(
                            f'<div style="background:{cor};padding:8px 12px;border-radius:8px;margin-bottom:6px;font-size:12px;">'
                            f'<div style="color:#6B7280;">{emoji} <b>{hora}</b> · {tipo}</div>'
                            f'<div style="color:#111827;margin-top:2px;">{obs}</div>'
                            f'<div style="color:#4B5563;margin-top:2px;font-style:italic;">{conteudo[:200]}</div>'
                            '</div>',
                            unsafe_allow_html=True
                        )

    # ── Gráfico agregado ──
    # v1.1: gráfico reflete os MESMOS filtros aplicados (data, status, unidade, busca)
    st.divider()
    st.markdown("### 📊 Distribuição por status")
    if usar_data and data_de and data_ate and data_de <= data_ate:
        st.caption(
            f"📅 Considerando sessões de {data_de.strftime('%d/%m/%Y')} a "
            f"{data_ate.strftime('%d/%m/%Y')}"
        )
    contagem = df_f["status"].value_counts()
    if not contagem.empty:
        df_g = pd.DataFrame({
            "Status": [STATUS_LABEL.get(s, s) for s in contagem.index],
            "Qtd": contagem.values
        })
        fig = px.bar(df_g, x="Qtd", y="Status", orientation="h",
                     color_discrete_sequence=["#5BC0BE"], text="Qtd")
        fig.update_traces(textposition="outside")
        fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=30),
                          yaxis_title=None, xaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
