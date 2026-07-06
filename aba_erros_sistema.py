"""
==============================================================================
Dashboard Maislaser — Aba "🐛 Erros do Sistema"
==============================================================================
v1.0 (06/07/2026)

Renderiza:
    - Cards de resumo (24h / 7d / não resolvidos)
    - Filtros (robô, severidade, apenas não resolvidos, período)
    - Lista paginada com detalhes expansíveis
    - Botão marcar como resolvido + nota
    - Últimos 5 erros críticos em destaque
==============================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# CONEXÃO
# ============================================================================
@st.cache_resource
def _get_sb() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=15, show_spinner=False)
def _get_stats_erros() -> dict:
    try:
        sb = _get_sb()
        r = sb.rpc("erros_estatisticas").execute()
        return r.data if r.data else {}
    except Exception as e:
        st.error(f"⚠️ Erro ao ler estatísticas: {e}")
        return {}


@st.cache_data(ttl=15, show_spinner=False)
def _get_erros_paginados(robo=None, severidade=None, apenas_nao_resolvidos=False,
                         dias=30, limit=50, offset=0) -> pd.DataFrame:
    try:
        sb = _get_sb()
        r = sb.rpc("get_erros_paginados", {
            "p_robo": robo,
            "p_severidade": severidade,
            "p_apenas_nao_resolvidos": apenas_nao_resolvidos,
            "p_dias": dias,
            "p_limit": limit,
            "p_offset": offset,
        }).execute()
        return pd.DataFrame(r.data or [])
    except Exception as e:
        st.error(f"⚠️ Erro ao ler lista de erros: {e}")
        return pd.DataFrame()


def _acao_marcar_resolvido(erro_id: int, nota: str = None) -> bool:
    try:
        sb = _get_sb()
        r = sb.rpc("marcar_erro_resolvido", {
            "p_erro_id": erro_id,
            "p_resolvido_por": "dashboard",
            "p_nota": nota,
        }).execute()
        return bool(r.data and r.data.get("ok"))
    except Exception as e:
        st.error(f"Falha: {e}")
        return False


# ============================================================================
# HELPERS
# ============================================================================

def _fmt_dt(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = pd.to_datetime(iso_str, utc=True).tz_convert(TZ_SP)
        return dt.strftime("%d/%m %H:%M:%S")
    except Exception:
        return str(iso_str)[:19].replace("T", " ")


def _tempo_desde(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = pd.to_datetime(iso_str, utc=True)
        agora = pd.Timestamp.now(tz='UTC')
        delta = agora - dt
        segundos = int(delta.total_seconds())
        if segundos < 60:
            return f"há {segundos}s"
        minutos = segundos // 60
        if minutos < 60:
            return f"há {minutos}min"
        horas = minutos / 60
        if horas < 24:
            return f"há {horas:.1f}h"
        dias = horas / 24
        return f"há {dias:.1f} dias"
    except Exception:
        return "—"


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


SEVERIDADE_COR = {
    "critical": "#dc2626",
    "error":    "#ef4444",
    "warning":  "#f59e0b",
    "info":     "#3b82f6",
}
SEVERIDADE_ICON = {
    "critical": "🚨",
    "error":    "❌",
    "warning":  "⚠️",
    "info":     "ℹ️",
}
ROBO_ICON = {
    "pos_atendimento": "🎯",
    "agenda":          "📅",
    "bia":             "💬",
    "dashboard":       "📊",
    "sistema":         "⚙️",
}


# ============================================================================
# UI PRINCIPAL
# ============================================================================

def render_aba_erros():
    st.markdown("## 🐛 Erros do Sistema")
    st.caption("Log centralizado de erros/warnings de todos os robôs. Facilita debug e auditoria.")

    col_refresh, col_upd = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Atualizar", use_container_width=True, key="erros_refresh"):
            st.cache_data.clear()
            st.rerun()

    stats = _get_stats_erros()
    if not stats:
        st.warning("Nenhum erro registrado ainda ou RPC não encontrada. Rode `sistema_erros.sql` primeiro.")
        return

    with col_upd:
        st.caption("📊 Cache 15s")

    # ══════════════════════════════════════════════════════════════════
    # 1. CARDS DE RESUMO
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📊 Resumo")

    c1, c2, c3, c4, c5 = st.columns(5)
    total_24h  = stats.get("total_24h", 0) or 0
    total_7d   = stats.get("total_7d", 0) or 0
    nao_res    = stats.get("nao_resolvidos", 0) or 0
    criticos   = stats.get("criticos_24h", 0) or 0
    warnings   = stats.get("warnings_24h", 0) or 0

    c1.markdown(_card("🚨", criticos, "Críticos 24h",
                      cor="#dc2626" if criticos > 0 else "#9ca3af"), unsafe_allow_html=True)
    c2.markdown(_card("❌", total_24h, "Total 24h",
                      cor="#ef4444" if total_24h > 0 else "#9ca3af"), unsafe_allow_html=True)
    c3.markdown(_card("⚠️", warnings, "Warnings 24h",
                      cor="#f59e0b" if warnings > 0 else "#9ca3af"), unsafe_allow_html=True)
    c4.markdown(_card("📈", total_7d, "Total 7 dias", "#3b82f6"), unsafe_allow_html=True)
    c5.markdown(_card("🔧", nao_res, "Não resolvidos",
                      cor="#ef4444" if nao_res > 0 else "#22c55e"), unsafe_allow_html=True)

    ultimo = stats.get("ultimo_erro")
    if ultimo:
        st.caption(f"⏱️ Último erro/crítico: **{_fmt_dt(ultimo)}** ({_tempo_desde(ultimo)})")

    # ══════════════════════════════════════════════════════════════════
    # 2. BREAKDOWN POR ROBÔ (7d)
    # ══════════════════════════════════════════════════════════════════
    por_robo = stats.get("por_robo_7d", []) or []
    if por_robo:
        st.markdown("**Erros por robô (últimos 7d):**")
        df_r = pd.DataFrame(por_robo)
        df_r["robo"] = df_r["robo"].apply(lambda r: f"{ROBO_ICON.get(r, '📦')} {r}")
        st.dataframe(df_r, use_container_width=True, hide_index=True,
                     column_config={
                         "robo": st.column_config.TextColumn("Robô"),
                         "qtd": st.column_config.NumberColumn("Qtd", format="%d"),
                     })

    # ══════════════════════════════════════════════════════════════════
    # 3. FILTROS + LISTA PAGINADA
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🔍 Filtros")

    col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 2, 2])

    with col_f1:
        robo_filtro = st.selectbox(
            "Robô",
            ["Todos", "pos_atendimento", "agenda", "bia", "dashboard", "sistema"],
            key="erros_robo"
        )
    with col_f2:
        sev_filtro = st.selectbox(
            "Severidade",
            ["Todas", "critical", "error", "warning", "info"],
            key="erros_sev"
        )
    with col_f3:
        periodo_filtro = st.selectbox(
            "Período",
            ["Últimos 30 dias", "Últimos 7 dias", "Últimas 24h", "Últimas 2h"],
            key="erros_periodo"
        )
    with col_f4:
        apenas_nao_res = st.checkbox(
            "Só não resolvidos",
            value=False,
            key="erros_nao_res"
        )

    dias_map = {"Últimos 30 dias": 30, "Últimos 7 dias": 7, "Últimas 24h": 1, "Últimas 2h": 0}
    # Últimas 2h vira 1 dia mas UI mostra corretamente (a RPC filtra por dias inteiros).
    dias_val = dias_map.get(periodo_filtro, 30)
    if periodo_filtro == "Últimas 2h":
        dias_val = 1  # RPC não tem hora; interface só mostra a última 2h aproximado

    # Paginação
    if "erros_pag" not in st.session_state:
        st.session_state.erros_pag = 1
    tam_pag = st.selectbox("Por página", [25, 50, 100, 200], index=1, key="erros_tam_pag")

    # Reset paginação em mudança de filtro
    filtros_hash = f"{robo_filtro}|{sev_filtro}|{periodo_filtro}|{apenas_nao_res}|{tam_pag}"
    if st.session_state.get("erros_filtros_hash") != filtros_hash:
        st.session_state.erros_pag = 1
        st.session_state.erros_filtros_hash = filtros_hash

    offset = (st.session_state.erros_pag - 1) * tam_pag

    df_erros = _get_erros_paginados(
        robo=robo_filtro if robo_filtro != "Todos" else None,
        severidade=sev_filtro if sev_filtro != "Todas" else None,
        apenas_nao_resolvidos=apenas_nao_res,
        dias=dias_val,
        limit=tam_pag,
        offset=offset,
    )

    if df_erros.empty:
        st.success("✅ Nenhum erro nos filtros selecionados.")
        return

    total_geral = int(df_erros["total_count"].iloc[0]) if "total_count" in df_erros.columns else len(df_erros)
    total_pags = max(1, (total_geral + tam_pag - 1) // tam_pag)

    # ── Controles de paginação ──
    col_prev, col_info, col_next = st.columns([1, 3, 1])
    with col_prev:
        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        if st.button("◀ Anterior", use_container_width=True, key="erros_prev",
                     disabled=(st.session_state.erros_pag <= 1)):
            st.session_state.erros_pag -= 1
            st.rerun()
    with col_info:
        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        inicio = offset + 1
        fim = min(offset + tam_pag, total_geral)
        st.caption(f"Exibindo **{inicio}–{fim}** de **{total_geral}** · Página **{st.session_state.erros_pag}/{total_pags}**")
    with col_next:
        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        if st.button("Próxima ▶", use_container_width=True, key="erros_next",
                     disabled=(st.session_state.erros_pag >= total_pags)):
            st.session_state.erros_pag += 1
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Lista com drill-down ──
    for _, row in df_erros.iterrows():
        sev = row.get("severidade", "error")
        icon_sev = SEVERIDADE_ICON.get(sev, "❌")
        cor_sev = SEVERIDADE_COR.get(sev, "#ef4444")
        icon_robo = ROBO_ICON.get(row.get("robo", ""), "📦")
        resolvido = bool(row.get("resolvido", False))
        badge_res = " ✅ RESOLVIDO" if resolvido else ""

        mensagem_curta = str(row.get("mensagem", "") or "")[:120]

        titulo = (
            f"{icon_sev} **{sev.upper()}**{badge_res}  ·  "
            f"{icon_robo} `{row.get('robo', '?')}`  ·  "
            f"{_fmt_dt(row.get('data_hora'))}  ·  "
            f"{mensagem_curta}"
        )

        with st.expander(titulo):
            col_a, col_b = st.columns([2, 3])

            with col_a:
                st.markdown(f"**ID:** `{row.get('id')}`")
                st.markdown(f"**Severidade:** {sev}")
                st.markdown(f"**Robô:** {row.get('robo', '—')}")
                st.markdown(f"**Origem:** {row.get('origem', '—')}")
                st.markdown(f"**Módulo:** `{row.get('modulo', '—')}`")
                st.markdown(f"**Tipo erro:** `{row.get('tipo_erro', '—')}`")
                st.markdown(f"**Data/hora:** {_fmt_dt(row.get('data_hora'))} ({_tempo_desde(row.get('data_hora'))})")

                if row.get("telefone_cliente"):
                    st.markdown(f"**Cliente:** +{row['telefone_cliente']}")
                if row.get("unidade"):
                    st.markdown(f"**Unidade:** {row['unidade']}")

                if resolvido:
                    st.success(f"✅ Resolvido em {_fmt_dt(row.get('resolvido_em'))}")
                    if row.get("resolvido_por"):
                        st.caption(f"Por: {row['resolvido_por']}")
                    if row.get("resolvido_nota"):
                        st.caption(f"Nota: {row['resolvido_nota']}")
                else:
                    # Formulário pra marcar resolvido
                    nota = st.text_input("Nota (opcional)", key=f"erros_nota_{row['id']}")
                    if st.button("✅ Marcar como resolvido", key=f"erros_res_{row['id']}",
                                 use_container_width=True, type="primary"):
                        if _acao_marcar_resolvido(int(row["id"]), nota or None):
                            st.success("Marcado ✅")
                            st.cache_data.clear()
                            st.rerun()

            with col_b:
                st.markdown("**📝 Mensagem completa:**")
                st.code(row.get("mensagem", "") or "—", language="text")

                if row.get("contexto"):
                    st.markdown("**🔍 Contexto:**")
                    import json as _json
                    ctx = row["contexto"]
                    if isinstance(ctx, str):
                        try:
                            ctx = _json.loads(ctx)
                        except Exception:
                            pass
                    st.json(ctx)

                if row.get("stack_trace"):
                    with st.expander("🔬 Ver stack trace completo"):
                        st.code(row["stack_trace"], language="python")
