"""
==============================================================================
aba_saude.py — Aba de Saúde Consolidada do Sistema Maislaser
==============================================================================
Chama RPC saude_consolidada() no Supabase e renderiza 3 cards lado a lado:
  🟢/🟡/🔴 Agenda | Bia | Pós-atendimento

Cada card tem:
  • Semáforo verde/amarelo/vermelho calculado no SQL
  • Contadores rápidos (métricas)
  • Expander drill-down com alertas críticos, timeline, funis, etc.

Dependências:
  • streamlit >= 1.35 (produção roda 1.35.0)
  • supabase-py
  • pandas
Secrets:
  • SUPABASE_URL
  • SUPABASE_KEY

Uso em app.py:
    from aba_saude import render as render_saude
    ...
    # abaixo dos 3 botões de robô ativo:
    render_saude()

Cache: 60s (@st.cache_data). Botão "Atualizar agora" limpa o cache.
==============================================================================
"""

import streamlit as st
from supabase import create_client
from datetime import datetime, timezone
import pandas as pd


# ============================================================================
# Cliente Supabase (autocontido — não depende de outros módulos do dashboard)
# ============================================================================

@st.cache_resource
def _get_supabase():
    """Cria cliente Supabase 1x por sessão."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


@st.cache_data(ttl=60, show_spinner=False)
def _carregar_saude():
    """Chama RPC saude_consolidada() com cache de 60s."""
    sb = _get_supabase()
    resp = sb.rpc("saude_consolidada", {}).execute()
    return resp.data


# ============================================================================
# Constantes
# ============================================================================

CORES = {
    "verde":    "🟢",
    "amarelo":  "🟡",
    "vermelho": "🔴",
}


# ============================================================================
# Helpers
# ============================================================================

def _tempo_desde(iso_str) -> str:
    """Converte ISO timestamp em '2min atrás', '5h atrás', etc."""
    if not iso_str:
        return "—"
    try:
        s = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        agora = datetime.now(timezone.utc)
        segundos = int((agora - dt).total_seconds())
        if segundos < 0:
            return "agora"
        if segundos < 60:
            return f"{segundos}s atrás"
        if segundos < 3600:
            return f"{segundos // 60}min atrás"
        if segundos < 86400:
            return f"{segundos // 3600}h atrás"
        return f"{segundos // 86400}d atrás"
    except Exception:
        return str(iso_str)


def _df_from_dict(d: dict, cols=("Chave", "Valor")) -> pd.DataFrame:
    """Converte dict → DataFrame de 2 colunas (útil pra contadores)."""
    if not d:
        return pd.DataFrame(columns=list(cols))
    itens = [(k, v) for k, v in d.items() if not isinstance(v, (list, dict))]
    return pd.DataFrame(itens, columns=list(cols))


def _renderizar_arrays_criticos(ac: dict, mapa: list):
    """
    Renderiza cada array de alertas críticos como dataframe SÓ se não-vazio.
    `mapa` = lista de tuplas (label_exibicao, chave_no_dict).
    """
    for label, chave in mapa:
        arr = ac.get(chave) or []
        if arr and isinstance(arr, list):
            st.markdown(f"**{label}** ({len(arr)})")
            try:
                st.dataframe(pd.DataFrame(arr), hide_index=True, use_container_width=True)
            except Exception as e:
                st.write(arr)  # fallback bruto se DataFrame falhar


# ============================================================================
# Card AGENDA
# ============================================================================

def _card_agenda(dados: dict, semaforo: str):
    emoji = CORES.get(semaforo, "🟢")
    resumo = dados.get("resumo_executivo", {}) or {}
    saude = dados.get("saude", {}) or {}

    st.markdown(f"### {emoji} AGENDA")
    st.metric("Disparos hoje", resumo.get("disparos_hoje", 0))
    col1, col2 = st.columns(2)
    col1.metric("Ativos", resumo.get("ativos_agora", 0))
    col2.metric("Aguardando", resumo.get("aguardando_agora", 0))
    st.caption(f"Última interação: {_tempo_desde(saude.get('ultima_interacao'))}")

    with st.expander("Detalhes Agenda"):
        # Alertas críticos primeiro
        _renderizar_arrays_criticos(dados, [
            ("⚠️ Indicação travada 25h+", "indicacao_travada_25h"),
            ("⚠️ Aguardando travado 24h+", "aguardando_travado_24h"),
            ("⚠️ Duplicatas telefone",     "duplicatas_telefone"),
            ("⚠️ Erros sistema 7d",        "sistema_erros_7d"),
        ])

        # Último disparo
        ud = dados.get("ultimo_disparo") or {}
        if ud:
            st.markdown("**Último disparo**")
            st.write(f"📄 `{ud.get('arquivo', '—')}`")
            st.write(f"📍 {ud.get('unidade', '—')} · ✅ {ud.get('whatsapp_ok', 0)}/{ud.get('total_clientes', 0)}")
            st.write(f"⏱️ {_tempo_desde(ud.get('quando'))}")

        # Contadores status
        cs = dados.get("contadores_status") or {}
        if cs:
            st.markdown("**Status atuais**")
            st.dataframe(
                _df_from_dict(cs, ("Status", "Qtd")),
                hide_index=True, use_container_width=True,
            )

        # Timeline últimas 2h
        ints = dados.get("interacoes_ultimas_2h") or []
        if ints:
            st.markdown(f"**Últimas 2h ({len(ints)} interações)**")
            df = pd.DataFrame(ints)
            cols_uteis = ["quando", "nome", "unidade", "observacao",
                          "status_antes", "status_depois"]
            df = df[[c for c in cols_uteis if c in df.columns]]
            st.dataframe(df, hide_index=True, use_container_width=True, height=280)


# ============================================================================
# Card BIA
# ============================================================================

def _card_bia(dados: dict, semaforo: str):
    emoji = CORES.get(semaforo, "🟢")
    resumo = dados.get("resumo_executivo", {}) or {}
    ac = dados.get("alertas_criticos", {}) or {}
    saude = dados.get("saude", {}) or {}

    st.markdown(f"### {emoji} BIA")
    st.metric("Indicações hoje", resumo.get("indicacoes_hoje", 0))
    col1, col2 = st.columns(2)
    col1.metric("Ativos", resumo.get("ativos_agora", 0))
    col2.metric("Travados 48h", resumo.get("travados_48h", 0))
    st.caption(f"Última indicação: {_tempo_desde(saude.get('ultima_indicacao_recebida'))}")

    with st.expander("Detalhes Bia"):
        # Alertas críticos (destaque no topo — só aparecem se não-vazios)
        _renderizar_arrays_criticos(ac, [
            ("🚨 Voucher sem finalizado",     "voucher_sem_finalizado"),
            ("🚨 Bia puxou status errado",    "bia_puxou_status_errado"),
            ("🚨 Aguardando validação 48h+",  "aguardando_validacao_48h"),
            ("🚨 Aguardando contatos 48h+",   "aguardando_contatos_48h"),
            ("🚨 Aguardando privacidade 48h+", "aguardando_privacidade_48h"),
            ("🚨 Finalizado sem voucher",     "finalizado_sem_voucher"),
            ("🚨 Duplicatas telefone ativo",  "duplicatas_telefone_ativo"),
        ])

        # Erros sistema 7d
        erros = dados.get("erros_sistema_7d") or []
        if erros:
            st.markdown(f"**⚠️ Erros sistema 7d** ({len(erros)})")
            st.dataframe(pd.DataFrame(erros), hide_index=True, use_container_width=True)

        # Funil 7d
        f = dados.get("funil_7d", {}) or {}
        if f:
            st.markdown("**Funil 7d**")
            st.dataframe(
                _df_from_dict(f, ("Etapa", "Qtd")),
                hide_index=True, use_container_width=True,
            )

        # Recovery
        rec = dados.get("recovery", {}) or {}
        if rec:
            st.markdown("**Recovery pipeline**")
            rec_simples = {k: v for k, v in rec.items() if not isinstance(v, list)}
            st.dataframe(
                _df_from_dict(rec_simples, ("Métrica", "Qtd")),
                hide_index=True, use_container_width=True,
            )

        # Ranking funcionárias 7d
        rank = dados.get("ranking_funcionarias_7d") or []
        if rank:
            st.markdown(f"**Ranking funcionárias 7d**")
            st.dataframe(pd.DataFrame(rank), hide_index=True, use_container_width=True)

        # Status atuais
        cs = dados.get("contagens_clientes_status", {}) or {}
        if cs:
            st.markdown("**Status atuais**")
            st.dataframe(
                _df_from_dict(cs, ("Status", "Qtd")),
                hide_index=True, use_container_width=True,
            )

        # Blacklist + indicações agregadas
        bl = dados.get("blacklist", {}) or {}
        ind = dados.get("indicacoes", {}) or {}
        if bl or ind:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Blacklist**")
                st.write(f"Total: {bl.get('total_registros', 0):,}")
                st.write(f"24h: {bl.get('adicionados_ultimas_24h', 0)}")
            with col_b:
                st.markdown("**Indicações**")
                st.write(f"Ativas: {ind.get('total_ativas', 0):,}")
                st.write(f"24h: {ind.get('ultimas_24h', 0)}")
                st.write(f"7d: {ind.get('ultimas_7d', 0)}")


# ============================================================================
# Card PÓS-ATENDIMENTO
# ============================================================================

def _card_pos(dados: dict, semaforo: str):
    emoji = CORES.get(semaforo, "🟢")
    saude = dados.get("saude", {}) or {}
    saude_hoje = saude.get("hoje", {}) or {}
    alertas = dados.get("alertas", {}) or {}
    ac = dados.get("alertas_criticos", {}) or {}
    r1 = dados.get("r1", {}) or {}
    con = dados.get("conectividade", {}) or {}

    st.markdown(f"### {emoji} PÓS-ATENDIMENTO")
    st.metric("Templates hoje", saude_hoje.get("templates_enviados", 0))
    col1, col2 = st.columns(2)
    col1.metric("Alertas hoje", alertas.get("total_hoje", 0))
    col2.metric("R1 hoje", saude_hoje.get("r1_enviados", 0))
    st.caption(f"Última resposta Meta: {_tempo_desde(con.get('ultima_saida_meta_ok'))}")

    with st.expander("Detalhes Pós"):
        # Alertas críticos
        _renderizar_arrays_criticos(ac, [
            ("🚨 R1 atrasados",             "r1_atrasados"),
            ("🚨 Presos pós-R1",            "presos_pos_r1"),
            ("🚨 Falhas envio 24h",         "falhas_envio_24h"),
            ("🚨 Sessões duplicadas",       "sessoes_duplicadas"),
            ("🚨 Presos template enviado",  "presos_template_enviado"),
        ])

        # Último disparo
        ud = dados.get("ultimo_disparo") or {}
        if ud:
            st.markdown("**Último disparo**")
            st.write(f"📄 `{ud.get('arquivo', '—')}` · fase: {ud.get('fase', '—')}")
            st.write(f"📍 {ud.get('unidade', '—')} · ✅ {ud.get('template_enviados_ok', 0)}/{ud.get('total_clientes_unicos', 0)}")
            st.write(f"⏱️ {_tempo_desde(ud.get('criado_em'))}")

        # Funil 7d
        f = dados.get("funil_7d", {}) or {}
        if f:
            st.markdown("**Funil 7d**")
            st.dataframe(
                _df_from_dict(f, ("Etapa", "Qtd")),
                hide_index=True, use_container_width=True,
            )

        # R1 detalhado
        if r1:
            st.markdown("**R1 detalhado**")
            r1_simples = {k: v for k, v in r1.items() if not isinstance(v, (list, dict))}
            st.dataframe(
                _df_from_dict(r1_simples, ("Métrica", "Valor")),
                hide_index=True, use_container_width=True,
            )

        # Alertas por motivo
        por_motivo = alertas.get("por_motivo_7d") or []
        if por_motivo:
            st.markdown("**Alertas por motivo (7d)**")
            st.dataframe(pd.DataFrame(por_motivo), hide_index=True, use_container_width=True)

        # Breakdown status
        bs = dados.get("breakdown_status") or []
        if bs:
            st.markdown("**Status últimos 7d**")
            st.dataframe(pd.DataFrame(bs), hide_index=True, use_container_width=True)

        # Breakdown unidade
        bu = dados.get("breakdown_unidade_7d") or []
        if bu:
            st.markdown("**Split unidades (7d)**")
            st.dataframe(pd.DataFrame(bu), hide_index=True, use_container_width=True)

        # Conectividade
        if con:
            st.markdown("**Conectividade**")
            st.write(f"Erros 24h: {con.get('total_erros_24h', 0)}")
            st.write(f"Último webhook: {_tempo_desde(con.get('ultima_entrada_webhook'))}")
            st.write(f"Última resposta Meta OK: {_tempo_desde(con.get('ultima_saida_meta_ok'))}")


# ============================================================================
# Entrada pública — chamar em app.py
# ============================================================================

def render():
    """Renderiza a aba de saúde consolidada. Chamar em app.py."""
    st.markdown("## 🩺 Saúde do Sistema")

    # Botão atualizar + timestamp
    col_btn, col_ts = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 Atualizar agora", key="saude_refresh_btn"):
            _carregar_saude.clear()
            st.rerun()

    # Carrega dados
    try:
        payload = _carregar_saude()
    except Exception as e:
        st.error(f"Erro ao carregar RPC saude_consolidada(): {e}")
        st.caption("Verifique se a RPC foi criada no Supabase.")
        return

    if not payload:
        st.warning("RPC saude_consolidada() retornou vazio.")
        return

    with col_ts:
        st.caption(f"Atualizado {_tempo_desde(payload.get('gerado_em'))}")

    st.divider()

    # 3 cards lado a lado
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        agenda = payload.get("agenda", {}) or {}
        _card_agenda(
            agenda.get("diagnostico", {}) or {},
            agenda.get("semaforo", "verde"),
        )
    with col_b:
        bia = payload.get("bia", {}) or {}
        _card_bia(
            bia.get("diagnostico", {}) or {},
            bia.get("semaforo", "verde"),
        )
    with col_c:
        pos = payload.get("pos", {}) or {}
        _card_pos(
            pos.get("diagnostico", {}) or {},
            pos.get("semaforo", "verde"),
        )
