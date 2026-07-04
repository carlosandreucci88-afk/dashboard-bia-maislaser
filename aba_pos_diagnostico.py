"""
==============================================================================
ROBÔ PÓS-ATENDIMENTO — Aba "🔧 Diagnóstico"
==============================================================================
v1.0 (04/07/2026)

Consome RPC pos_diagnostico_completo() do Supabase.
Renderiza:
    - Alertas críticos (com botões de ação)
    - Saúde geral (hoje / 7d / 30d)
    - Breakdown FSM (status atuais)
    - R1 (lembretes)
    - Alertas coord (por motivo)
    - Conectividade Meta + webhook
    - Config atual
    - Último disparo

Cache curto (15s) — chamar de novo é barato.
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
def _get_diagnostico() -> dict:
    try:
        sb = _get_sb()
        r = sb.rpc("pos_diagnostico_completo").execute()
        return r.data if r.data else {}
    except Exception as e:
        st.error(f"⚠️ Erro ao ler diagnóstico: {e}")
        return {}


def _acao_expirar(cliente_id: int) -> bool:
    try:
        sb = _get_sb()
        r = sb.rpc("pos_expirar_manual", {"p_cliente_id": cliente_id}).execute()
        return bool(r.data and r.data.get("ok"))
    except Exception as e:
        st.error(f"Falha: {e}")
        return False


def _acao_forcar_r1(cliente_id: int) -> bool:
    try:
        sb = _get_sb()
        r = sb.rpc("pos_forcar_r1_agora", {"p_cliente_id": cliente_id}).execute()
        return bool(r.data and r.data.get("ok"))
    except Exception as e:
        st.error(f"Falha: {e}")
        return False


# ============================================================================
# HELPERS DE FORMATAÇÃO
# ============================================================================

def _fmt_dt(iso_str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = pd.to_datetime(iso_str, utc=True).tz_convert(TZ_SP)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return str(iso_str)[:16].replace("T", " ")


def _tempo_desde(iso_str) -> str:
    """Retorna string tipo 'há 3h', 'há 2 dias'."""
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


def _card_metric(icon: str, valor, label: str, cor: str = "#5BC0BE", sub: str = None) -> str:
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


def _badge(texto: str, cor: str = "#5BC0BE") -> str:
    return (
        f'<span style="background:{cor}1A;color:{cor};padding:3px 10px;border-radius:999px;'
        f'font-size:11px;font-weight:600;">{texto}</span>'
    )


# ============================================================================
# UI PRINCIPAL
# ============================================================================

def render_aba_pos_diagnostico():
    st.markdown("## 🔧 Diagnóstico do Sistema")
    st.caption("Saúde geral, alertas críticos e ações rápidas pra desengasgar clientes travados.")

    col_refresh, col_upd = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Atualizar", use_container_width=True, key="pos_diag_refresh"):
            st.cache_data.clear()
            st.rerun()

    diag = _get_diagnostico()
    if not diag:
        st.error("Não consegui ler o diagnóstico. Verifica se a RPC `pos_diagnostico_completo` existe no Supabase.")
        return

    with col_upd:
        gerado_em = _fmt_dt(diag.get("gerado_em"))
        st.caption(f"📊 Dados atualizados: **{gerado_em}**  ·  cache 15s")

    # ══════════════════════════════════════════════════════════════════
    # 1. ALERTAS CRÍTICOS
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🚨 Alertas Críticos")

    alertas = diag.get("alertas_criticos", {}) or {}
    presos_template = alertas.get("presos_template_enviado", []) or []
    r1_atrasados = alertas.get("r1_atrasados", []) or []
    presos_pos_r1 = alertas.get("presos_pos_r1", []) or []
    falhas_envio = alertas.get("falhas_envio_24h", []) or []
    sessoes_dup = alertas.get("sessoes_duplicadas", []) or []
    sistema_off = alertas.get("sistema_desligado", False)
    modo_manut = alertas.get("modo_manutencao_ativo", False)

    total_alertas = (
        int(sistema_off) + int(modo_manut) +
        len(presos_template) + len(r1_atrasados) + len(presos_pos_r1) +
        len(falhas_envio) + len(sessoes_dup)
    )

    if total_alertas == 0:
        st.success("✅ Nenhum alerta crítico. Sistema saudável.")
    else:
        st.error(f"⚠️ **{total_alertas} alerta(s) crítico(s) detectado(s)**")

        # Kill switches
        if sistema_off:
            st.error("🔴 **Sistema DESLIGADO** — `pos_habilitado=false` no Supabase. Robô não processa mensagens.")
        if modo_manut:
            st.error("🔴 **Modo manutenção ATIVO** — todos os robôs estão pausados.")

        # Presos em template_enviado > 24h
        if presos_template:
            with st.expander(f"⏰ {len(presos_template)} cliente(s) presos em `template_enviado` há > 24h", expanded=True):
                st.caption("Cliente recebeu template mas nunca respondeu. Se responder agora, será expirado — mas se nunca responder, fica assim pra sempre.")
                for c in presos_template:
                    col_info, col_acao = st.columns([4, 1])
                    with col_info:
                        st.markdown(
                            f"**{c['nome']}** · {c['unidade'].replace('Mogi das Cruzes', 'Mogi') if c.get('unidade') else '?'} · "
                            f"+{c['telefone']} · parado há **{c['horas_parado']}h**"
                        )
                    with col_acao:
                        if st.button("⏰ Expirar", key=f"expirar_tpl_{c['id']}", use_container_width=True):
                            if _acao_expirar(c['id']):
                                st.success("Expirado ✅")
                                st.cache_data.clear()
                                st.rerun()

        # R1 atrasados (bug do trigger?)
        if r1_atrasados:
            with st.expander(f"🔔 {len(r1_atrasados)} cliente(s) elegíveis pra R1 mas ainda não receberam", expanded=True):
                st.caption("Trigger de 30min pode ter falhado. Aguarde próximo ciclo ou force manualmente.")
                for c in r1_atrasados:
                    col_info, col_acao = st.columns([4, 1])
                    with col_info:
                        st.markdown(
                            f"**{c['nome']}** · {c['unidade'].replace('Mogi das Cruzes', 'Mogi') if c.get('unidade') else '?'} · "
                            f"+{c['telefone']} · pendente há **{c['horas_pendente']}h**"
                        )
                    with col_acao:
                        if st.button("🔔 Forçar R1", key=f"r1_{c['id']}", use_container_width=True):
                            if _acao_forcar_r1(c['id']):
                                st.success("Marcado ✅ — próximo trigger envia")
                                st.cache_data.clear()
                                st.rerun()

        # Presos após R1 > 24h
        if presos_pos_r1:
            with st.expander(f"⌛ {len(presos_pos_r1)} cliente(s) receberam R1 há > 24h e não responderam"):
                st.caption("Comportamento esperado — cliente sumiu. Pode expirar manualmente pra limpar.")
                for c in presos_pos_r1:
                    col_info, col_acao = st.columns([4, 1])
                    with col_info:
                        st.markdown(
                            f"**{c['nome']}** · {c['unidade'].replace('Mogi das Cruzes', 'Mogi') if c.get('unidade') else '?'} · "
                            f"+{c['telefone']} · R1 enviado há **{c['horas_desde_r1']}h**"
                        )
                    with col_acao:
                        if st.button("⏰ Expirar", key=f"expirar_r1_{c['id']}", use_container_width=True):
                            if _acao_expirar(c['id']):
                                st.success("Expirado ✅")
                                st.cache_data.clear()
                                st.rerun()

        # Falhas de envio nas últimas 24h
        if falhas_envio:
            with st.expander(f"❌ {len(falhas_envio)} falha(s) de envio nas últimas 24h"):
                st.caption("Templates que retornaram erro do Meta. Verifica se template ainda tá aprovado.")
                for c in falhas_envio:
                    st.markdown(
                        f"**{c['nome']}** · {c.get('unidade', '?')} · +{c['telefone']} · em {_fmt_dt(c['ultima_atualizacao'])}"
                    )

        # Sessões duplicadas
        if sessoes_dup:
            with st.expander(f"🔴 {len(sessoes_dup)} telefone(s) com múltiplas sessões ativas"):
                st.caption("Dedup falhou — telefone com > 1 sessão em status ativo. Isso NÃO deveria acontecer com o fix v1.1.")
                for s in sessoes_dup:
                    st.markdown(f"**+{s['telefone']}** — {s['qtd_sessoes_ativas']} sessões ativas")

    # ══════════════════════════════════════════════════════════════════
    # 2. SAÚDE GERAL (hoje / 7d / 30d)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📊 Saúde Geral")

    saude = diag.get("saude", {}) or {}
    hoje = saude.get("hoje", {}) or {}
    d7 = saude.get("ultimos_7d", {}) or {}
    d30 = saude.get("ultimos_30d", {}) or {}

    tab_hoje, tab_7d, tab_30d = st.tabs(["📅 Hoje", "📆 Últimos 7 dias", "🗓️ Últimos 30 dias"])

    with tab_hoje:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.markdown(_card_metric("👥", hoje.get("clientes_novos", 0), "Novos clientes", "#5BC0BE"), unsafe_allow_html=True)
        c2.markdown(_card_metric("📤", hoje.get("templates_enviados", 0), "Templates", "#3b82f6"), unsafe_allow_html=True)
        c3.markdown(_card_metric("💬", hoje.get("respostas", 0), "Respostas", "#22c55e"), unsafe_allow_html=True)
        c4.markdown(_card_metric("🚨", hoje.get("alertas_coord", 0), "Alertas coord", "#f59e0b"), unsafe_allow_html=True)
        c5.markdown(_card_metric("🔔", hoje.get("r1_enviados", 0), "R1 enviados", "#a855f7"), unsafe_allow_html=True)

    with tab_7d:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.markdown(_card_metric("👥", d7.get("clientes_novos", 0), "Novos clientes", "#5BC0BE"), unsafe_allow_html=True)
        c2.markdown(_card_metric("📤", d7.get("templates_enviados", 0), "Templates", "#3b82f6"), unsafe_allow_html=True)
        c3.markdown(_card_metric("💬", d7.get("respostas", 0), "Respostas", "#22c55e"), unsafe_allow_html=True)
        c4.markdown(_card_metric("🔔", d7.get("r1_enviados", 0), "R1 enviados", "#a855f7"), unsafe_allow_html=True)
        c5.markdown(_card_metric("⏰", d7.get("expirados_24h", 0), "Expirados 24h", "#94a3b8"), unsafe_allow_html=True)

    with tab_30d:
        c1, c2, c3 = st.columns(3)
        c1.markdown(_card_metric("👥", d30.get("clientes_novos", 0), "Novos clientes", "#5BC0BE"), unsafe_allow_html=True)
        c2.markdown(_card_metric("📤", d30.get("templates_enviados", 0), "Templates", "#3b82f6"), unsafe_allow_html=True)
        c3.markdown(_card_metric("💬", d30.get("respostas", 0), "Respostas", "#22c55e"), unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════
    # 3. FUNIL DE CONVERSÃO 7d
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🎯 Funil de Conversão (7 dias)")

    funil = diag.get("funil_7d", {}) or {}
    tpls = funil.get("templates", 0) or 0
    resps = funil.get("respostas", 0) or 0
    tudo_otimo = funil.get("tudo_otimo", 0) or 0
    problemas = funil.get("problemas", 0) or 0
    cupom = funil.get("cupom_solicitado", 0) or 0

    tx_resp = f"{(resps/tpls*100):.1f}%" if tpls > 0 else "0%"
    tx_otimo = f"{(tudo_otimo/resps*100):.1f}%" if resps > 0 else "0%"
    tx_cupom = f"{(cupom/tudo_otimo*100):.1f}%" if tudo_otimo > 0 else "0%"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_card_metric("📤", tpls, "Templates", "#3b82f6"), unsafe_allow_html=True)
    c2.markdown(_card_metric("💬", resps, "Responderam", "#22c55e", sub=f"{tx_resp} do total"), unsafe_allow_html=True)
    c3.markdown(_card_metric("🌟", tudo_otimo, "Tudo ótimo", "#22c55e", sub=f"{tx_otimo} das respostas"), unsafe_allow_html=True)
    c4.markdown(_card_metric("🎁", cupom, "Cupom pedido", "#a855f7", sub=f"{tx_cupom} dos satisfeitos"), unsafe_allow_html=True)
    c5.markdown(_card_metric("🚨", problemas, "Problemas", "#ef4444"), unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════
    # 4. BREAKDOWN FSM (todos os status atuais)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🔀 Distribuição de Status (base completa)")

    breakdown = diag.get("breakdown_status", []) or []
    if breakdown:
        df_break = pd.DataFrame(breakdown)
        st.dataframe(
            df_break,
            use_container_width=True,
            hide_index=True,
            column_config={
                "status": st.column_config.TextColumn("Status"),
                "qtd": st.column_config.NumberColumn("Qtd", format="%d"),
            }
        )
    else:
        st.caption("Sem dados.")

    # ══════════════════════════════════════════════════════════════════
    # 5. R1 (LEMBRETES)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🔔 R1 — Lembretes (2h)")

    r1 = diag.get("r1", {}) or {}
    ultimo_r1 = r1.get("ultimo_r1_disparado")
    taxa_conv_r1 = r1.get("taxa_conversao_pos_r1_7d")

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_card_metric("⏳", r1.get("total_pendentes_agora", 0), "Pendentes agora", "#f59e0b"), unsafe_allow_html=True)
    c2.markdown(_card_metric("📨", r1.get("total_enviados_hoje", 0), "R1 hoje", "#5BC0BE"), unsafe_allow_html=True)
    c3.markdown(_card_metric("📈", r1.get("total_enviados_7d", 0), "R1 últimos 7d", "#3b82f6"), unsafe_allow_html=True)
    conv_str = f"{taxa_conv_r1}%" if taxa_conv_r1 is not None else "—"
    c4.markdown(_card_metric("🎯", conv_str, "Conversão pós-R1 7d", "#22c55e"), unsafe_allow_html=True)

    if ultimo_r1:
        st.caption(f"⏱️ Último R1 disparado: **{_fmt_dt(ultimo_r1)}** ({_tempo_desde(ultimo_r1)})")

    # ══════════════════════════════════════════════════════════════════
    # 6. ALERTAS COORD (por motivo)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🚨 Alertas para Coordenadora")

    alrt = diag.get("alertas", {}) or {}
    por_motivo = alrt.get("por_motivo_7d", []) or []

    c1, c2 = st.columns(2)
    c1.markdown(_card_metric("📤", alrt.get("total_hoje", 0), "Alertas hoje", "#ef4444"), unsafe_allow_html=True)
    c2.markdown(_card_metric("📊", alrt.get("total_7d", 0), "Alertas últimos 7d", "#f59e0b"), unsafe_allow_html=True)

    if por_motivo:
        st.markdown("**Breakdown 7d por motivo:**")
        df_mot = pd.DataFrame(por_motivo)
        st.dataframe(
            df_mot,
            use_container_width=True,
            hide_index=True,
            column_config={
                "motivo": st.column_config.TextColumn("Motivo"),
                "qtd": st.column_config.NumberColumn("Qtd", format="%d"),
            }
        )

    # ══════════════════════════════════════════════════════════════════
    # 7. CONECTIVIDADE (Meta + Webhook)
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🔌 Conectividade")

    conn = diag.get("conectividade", {}) or {}
    ult_webhook = conn.get("ultima_entrada_webhook")
    ult_saida = conn.get("ultima_saida_meta_ok")
    ult_erro = conn.get("ultimo_erro_meta")
    total_erros = conn.get("total_erros_24h", 0) or 0
    ultimos_erros = conn.get("ultimos_5_erros", []) or []

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        _card_metric("📥", _tempo_desde(ult_webhook), "Última entrada",
                     cor="#22c55e" if ult_webhook else "#94a3b8",
                     sub=_fmt_dt(ult_webhook)),
        unsafe_allow_html=True
    )
    c2.markdown(
        _card_metric("📤", _tempo_desde(ult_saida), "Última saída OK",
                     cor="#22c55e" if ult_saida else "#94a3b8",
                     sub=_fmt_dt(ult_saida)),
        unsafe_allow_html=True
    )
    c3.markdown(
        _card_metric("❌", total_erros, "Erros últimas 24h",
                     cor="#ef4444" if total_erros > 0 else "#94a3b8"),
        unsafe_allow_html=True
    )

    if ultimos_erros:
        with st.expander(f"🔍 Ver últimos {len(ultimos_erros)} erros do Meta"):
            for e in ultimos_erros:
                st.markdown(
                    f"**{_fmt_dt(e['data_hora'])}** · +{e['telefone']}  \n"
                    f"`{e['observacao'][:250]}`"
                )
                st.divider()

    # ══════════════════════════════════════════════════════════════════
    # 8. CONFIG ATUAL
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### ⚙️ Configuração Atual")

    cfg = diag.get("config", {}) or {}

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**🚦 Estado do sistema**")
        habilitado = cfg.get("pos_habilitado")
        manut = cfg.get("modo_manutencao")
        st.markdown(f"- `pos_habilitado`: {'✅ ligado' if habilitado else '🔴 DESLIGADO'}")
        st.markdown(f"- `modo_manutencao`: {'🔴 ATIVO' if manut else '✅ inativo'}")
        st.markdown(f"- Janela horário: **{cfg.get('janela_inicio', '?')}h–{cfg.get('janela_fim', '?')}h**")
        st.markdown(f"- Cupom atual: `{cfg.get('codigo_cupom', '—')}`")

    with c2:
        st.markdown("**👥 Coordenadoras**")
        st.markdown(f"- **Mogi**: {cfg.get('coord_mogi_nome', '?')} · `+{cfg.get('coord_mogi_tel', '?')}`")
        st.markdown(f"- **Suzano**: {cfg.get('coord_suzano_nome', '?')} · `+{cfg.get('coord_suzano_tel', '?')}`")
        review_m = cfg.get('review_mogi', '')
        review_s = cfg.get('review_suzano', '')
        if review_m:
            st.markdown(f"- Review Mogi: [{review_m[:40]}...]({review_m})")
        if review_s:
            st.markdown(f"- Review Suzano: [{review_s[:40]}...]({review_s})")

    # ══════════════════════════════════════════════════════════════════
    # 9. ÚLTIMO DISPARO
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📦 Último Disparo")

    ult_disp = diag.get("ultimo_disparo")
    if ult_disp:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("📅 Quando", _fmt_dt(ult_disp.get("criado_em")))
        c2.metric("📍 Unidade", (ult_disp.get("unidade") or "?").replace("Mogi das Cruzes", "Mogi"))
        c3.metric("✅ Enviados", ult_disp.get("template_enviados_ok", 0))
        c4.metric("❌ Erros", ult_disp.get("erros_envio", 0))

        arq = ult_disp.get("arquivo") or "—"
        fase = ult_disp.get("fase") or "?"
        st.caption(f"📄 Arquivo: `{arq}`  ·  Fase: **{fase}**  ·  Total únicos: {ult_disp.get('total_clientes_unicos', 0)}")
    else:
        st.info("Nenhum disparo registrado ainda.")
