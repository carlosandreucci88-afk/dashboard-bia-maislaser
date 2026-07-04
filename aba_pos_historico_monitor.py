"""
==============================================================================
ROBÔ PÓS-ATENDIMENTO — Abas "Histórico de disparos" + "Monitoramento clientes"
==============================================================================
v1.0 (04/07/2026)

Parte B — render_aba_pos_historico():
    Lista de disparos passados. Métricas agregadas + drill-down.

Parte C — render_aba_pos_monitor():
    Cards de saúde (NPS, cupons, respostas)
    Lista filtrável de clientes (status, unidade, período, busca)
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
    "tudo_otimo_cupom_agora":    "🎁 Cupom AGORA (satisfeito)",
    "tudo_otimo_cupom_depois":   "✅ Satisfeito (cupom depois)",
    "cupom_agora_direto":        "🎁 Cupom direto do template",
    "problema_atendimento":      "🔴 Problema atendimento",
    "resultado_ruim":            "🔴 Resultado ruim",
    "redirecionado_coordenadora":"⚠️ Redirecionado (inválidas)",
    "sem_resposta_24h":          "⚪ Sem resposta (24h)",
    "duplicata_ignorada":        "⏭️ Duplicata ignorada",
    "falha_envio":               "❌ Falha no envio",
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
def _get_clientes(limit: int = 500) -> pd.DataFrame:
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

    col_r, col_ln = st.columns([3, 1])
    with col_ln:
        limit = st.selectbox("Últimos", [30, 100, 300, 1000], index=1, key="pos_hist_limit")

    df = _get_historico_disparos(limit=limit)

    if df.empty:
        st.info("Nenhum disparo registrado ainda. Faça um upload na aba 🚀 Disparar Pós-atendimento.")
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
    df = _get_clientes(500)
    if df.empty:
        st.info("Nenhum cliente registrado ainda.")
        return

    # ── Filtros ──
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

    if df_f.empty:
        st.info("Nenhum cliente nos filtros selecionados.")
        return

    st.markdown(f"### {len(df_f)} cliente(s)")

    # ── Tabela com drill-down ──
    for _, row in df_f.head(50).iterrows():
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

    if len(df_f) > 50:
        st.caption(f"Mostrando 50 de {len(df_f)}. Use filtros ou busca pra refinar.")

    # ── Gráfico agregado ──
    st.divider()
    st.markdown("### 📊 Distribuição por status")
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
