"""
==============================================================================
ABA PENDÊNCIAS BIA — Casos abertos esperando contato da recepção
==============================================================================
Substitui a notificação WhatsApp avulsa (que sofria com janela 24h da Meta)
por um painel persistente onde a recepção entra e vê o que precisa atender.

Lê 2 fontes do Supabase:
  • bia_disparos      → status IN ['HANDOFF', 'HANDOFF_MED', 'HANDOFF_MEDICO']
  • agendamentos      → reagendamento_solicitado_em OR nao_vou_conseguir_em

Ambas filtradas por contatado_em IS NULL (pendência aberta).

Ação principal: botão "✓ Marcar contatado" grava timestamp em contatado_em.
Não muda o status original (preserva audit trail: "isso foi um HANDOFF_MED
que foi atendido em XX/YY").

Sub-abas (escopo Fase 4):
  💬 HANDOFF              — cliente confuso / preço / fora do script
  🩺 HANDOFF Médico       — triagem médica positiva
  🔄 Reagendar/Não vai    — clicou PRECISO REAGENDAR ou NÃO VOU CONSEGUIR

SKIP_BASE, ERRO_NUMERO_INVALIDO, BLOQUEADO_PELO_INDICADO → fora de escopo
(virão em fase posterior conforme decisão do dono do projeto).
==============================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone

# Reusa helpers já existentes (filtros, métricas, export) — mesma UX do resto do app
from aba_confirmacao import (
    _filtros_periodo_unidade,
    _render_metric_card_local,
    _botao_export_xlsx,
)

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# CONEXÃO SUPABASE — lazy import pra evitar circular com dashboard_maislaser.py
# ============================================================================

def _get_sb():
    """Lazy import do get_supabase pra evitar circular import.
    Quando esta função é chamada, dashboard_maislaser.py já está totalmente
    carregado, então o import funciona sem problemas."""
    from dashboard_maislaser import get_supabase
    return get_supabase()


# ============================================================================
# CARGAS DE DADOS — 2 queries no Supabase com cache 30s
# ============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def _carregar_pendencias_bia_disparos():
    """HANDOFF + HANDOFF_MED + HANDOFF_MEDICO pendentes (contatado_em IS NULL)."""
    sb = _get_sb()
    try:
        result = sb.table("bia_disparos").select(
            "id, telefone, nome_indicado, nome_cadastrante, unidade, status, "
            "desfecho_em, ultima_notif_recepcao, fila_em, criado_em"
        ).in_("status", ["HANDOFF", "HANDOFF_MED", "HANDOFF_MEDICO"]) \
         .is_("contatado_em", "null") \
         .order("desfecho_em", desc=True) \
         .limit(500).execute()

        df = pd.DataFrame(result.data)
        if df.empty:
            return df

        # Parse timestamps tz-aware
        for col in ('desfecho_em', 'ultima_notif_recepcao', 'fila_em', 'criado_em'):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)

        # Coluna unificada "quando virou pendência" (com fallback)
        df['quando'] = df['desfecho_em'].fillna(df['fila_em']).fillna(df['criado_em'])
        try:
            df['quando_sp'] = df['quando'].dt.tz_convert(TZ_SP)
        except Exception:
            df['quando_sp'] = df['quando']

        # Normaliza HANDOFF_MEDICO → HANDOFF_MED na UI (caso o cérebro use as 2 grafias)
        df['tipo'] = df['status'].replace({'HANDOFF_MEDICO': 'HANDOFF_MED'})

        # Padroniza nome pra display
        df['nome'] = df['nome_indicado'].fillna('Sem nome')

        return df
    except Exception as e:
        st.error(f"Erro ao carregar pendências (bia_disparos): {e}")
        return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def _carregar_pendencias_agendamentos():
    """Agendamentos com reagendamento_solicitado_em OU nao_vou_conseguir_em preenchidos,
    ainda sem contato da recepção."""
    sb = _get_sb()
    try:
        # PostgREST: OR de "is not null" via .or_()
        # Sintaxe: "col1.not.is.null,col2.not.is.null"
        result = sb.table("agendamentos").select(
            "id, telefone, nome, unidade, area, data_hora, status, "
            "reagendamento_solicitado_em, nao_vou_conseguir_em, criado_em"
        ).is_("contatado_em", "null") \
         .or_("reagendamento_solicitado_em.not.is.null,nao_vou_conseguir_em.not.is.null") \
         .limit(500).execute()

        df = pd.DataFrame(result.data)
        if df.empty:
            return df

        for col in ('reagendamento_solicitado_em', 'nao_vou_conseguir_em', 'data_hora', 'criado_em'):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)

        # "Quando pediu" = NAO_VOU tem prioridade (mais urgente que REAGENDAR)
        df['quando'] = df['nao_vou_conseguir_em'].fillna(df['reagendamento_solicitado_em'])
        try:
            df['quando_sp'] = df['quando'].dt.tz_convert(TZ_SP)
            if 'data_hora' in df.columns:
                df['data_hora_sp'] = df['data_hora'].dt.tz_convert(TZ_SP)
        except Exception:
            df['quando_sp'] = df['quando']
            df['data_hora_sp'] = df.get('data_hora')

        # Sub-tipo dentro de REAGENDAR pra UX (badge diferente)
        df['tipo'] = df['nao_vou_conseguir_em'].notna().map({True: 'NAO_VOU', False: 'REAGENDAR'})

        return df.sort_values('quando_sp', ascending=False)
    except Exception as e:
        st.error(f"Erro ao carregar pendências (agendamentos): {e}")
        st.caption(
            "💡 Se o erro for sobre `.or_()`, pode ser sintaxe do PostgREST. "
            "Avise o dev pra trocar pela abordagem de 2 queries + concat."
        )
        return pd.DataFrame()


# ============================================================================
# AÇÕES (mutações) — marcar contatado
# ============================================================================

def _marcar_contatado(tabela, registro_id):
    """Grava contatado_em = now() na tabela/id especificado.
    Limpa cache pra que a próxima carga reflita a mudança."""
    sb = _get_sb()
    try:
        agora_iso = datetime.now(TZ_SP).isoformat()
        sb.table(tabela).update({"contatado_em": agora_iso}).eq("id", registro_id).execute()
        # Invalida cache pra remover o item da lista no próximo rerun
        _carregar_pendencias_bia_disparos.clear()
        _carregar_pendencias_agendamentos.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao marcar contatado: {e}")
        return False


# ============================================================================
# HELPERS DE RENDER
# ============================================================================

def _tempo_relativo(ts_sp):
    """Timestamp SP → 'agora' / '5min' / '3h' / '12/06 14:30'."""
    if pd.isna(ts_sp):
        return "—"
    try:
        delta = datetime.now(TZ_SP) - ts_sp
        secs = delta.total_seconds()
        if secs < 60:
            return "agora"
        elif secs < 3600:
            return f"{int(secs / 60)}min"
        elif secs < 86400:
            return f"{int(secs / 3600)}h"
        else:
            return ts_sp.strftime('%d/%m %H:%M')
    except Exception:
        return "—"


def _norm_unidade_display(u):
    """Mogi das Cruzes → Mogi (encurta pra caber na coluna)."""
    if not isinstance(u, str) or not u.strip():
        return "—"
    return u.replace("Mogi das Cruzes", "Mogi")


# ============================================================================
# RENDER POR SUB-ABA
# ============================================================================

def _render_lista_handoffs(df, tipo_label, key_prefix):
    """Renderiza lista de HANDOFF ou HANDOFF_MED (vindos de bia_disparos)."""
    if df.empty:
        st.success(f"🎉 Nenhum {tipo_label} aberto!")
        return

    # Filtros (período + unidade) — default "Tudo" pra ver toda fila aberta
    df_f = _filtros_periodo_unidade(df, "quando_sp", "unidade", key_prefix, default_periodo="Tudo")

    if df_f.empty:
        st.info("Nenhum caso nos filtros selecionados.")
        return

    # Header + export
    col_t, col_e = st.columns([4, 1.4])
    with col_t:
        st.markdown(f"### {len(df_f)} caso(s) aberto(s)")
    with col_e:
        _botao_export_xlsx(df_f, f"pendencias_{key_prefix}", key_prefix)

    # Cabeçalho da tabela
    h1, h2, h3, h4, h5, h6 = st.columns([1.7, 1.2, 0.8, 1.3, 0.9, 1.5])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Indicado por**")
    h5.markdown("**Há**")
    h6.markdown("**Ação**")
    st.markdown('<hr style="margin: 4px 0 8px 0;">', unsafe_allow_html=True)

    for _, row in df_f.head(100).iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([1.7, 1.2, 0.8, 1.3, 0.9, 1.5])

        nome = row.get('nome') or '—'
        tel = str(row.get('telefone', '—'))
        unid = _norm_unidade_display(row.get('unidade'))
        ind_por = row.get('nome_cadastrante') or '—'
        quando_str = _tempo_relativo(row.get('quando_sp'))

        # Badge de subtipo no nome
        if row.get('tipo') == 'HANDOFF_MED':
            tipo_badge = '<span class="badge-purple">🩺 Médico</span>'
        else:
            tipo_badge = '<span class="badge-info">💬 HANDOFF</span>'

        c1.markdown(f"<div style='font-weight: 600;'>{nome}</div>{tipo_badge}", unsafe_allow_html=True)
        c2.markdown(f"<div style='font-size: 12px; color: #6B7280;'>+{tel}</div>", unsafe_allow_html=True)
        c3.write(unid)
        c4.markdown(f"<div style='font-size: 12px;'>{ind_por}</div>", unsafe_allow_html=True)
        c5.markdown(f"<div style='font-size: 12px; color: #9CA3AF;'>{quando_str}</div>", unsafe_allow_html=True)

        # Coluna de ação: WhatsApp + Marcar contatado
        with c6:
            sub_wa, sub_ok = st.columns([1, 1])
            with sub_wa:
                st.link_button("💬 WA", url=f"https://wa.me/{tel}", use_container_width=True)
            with sub_ok:
                if st.button("✓", key=f"contat_bd_{row['id']}", help="Marcar contatado", use_container_width=True):
                    if _marcar_contatado("bia_disparos", row['id']):
                        st.toast(f"✅ {nome} marcado como contatado", icon="✅")
                        st.rerun()

    if len(df_f) > 100:
        st.caption(f"Mostrando 100 de {len(df_f)}. Use os filtros pra refinar.")


def _render_lista_reagendar(df, key_prefix):
    """Renderiza lista REAGENDAR + NAO_VOU (vindos de agendamentos)."""
    if df.empty:
        st.success("🎉 Nenhuma pendência aberta!")
        return

    df_f = _filtros_periodo_unidade(df, "quando_sp", "unidade", key_prefix, default_periodo="Tudo")

    if df_f.empty:
        st.info("Nenhum caso nos filtros selecionados.")
        return

    col_t, col_e = st.columns([4, 1.4])
    with col_t:
        st.markdown(f"### {len(df_f)} caso(s) aberto(s)")
    with col_e:
        _botao_export_xlsx(df_f, "pendencias_reagendar", key_prefix)

    h1, h2, h3, h4, h5, h6, h7 = st.columns([1.7, 1.2, 0.8, 1.2, 1.0, 0.9, 1.5])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Sessão**")
    h5.markdown("**Área**")
    h6.markdown("**Pediu há**")
    h7.markdown("**Ação**")
    st.markdown('<hr style="margin: 4px 0 8px 0;">', unsafe_allow_html=True)

    for _, row in df_f.head(100).iterrows():
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.7, 1.2, 0.8, 1.2, 1.0, 0.9, 1.5])

        nome = row.get('nome') or '—'
        tel = str(row.get('telefone', '—'))
        unid = _norm_unidade_display(row.get('unidade'))
        area = row.get('area') or '—'
        quando_str = _tempo_relativo(row.get('quando_sp'))

        # Sessão original (importante pra recepção saber qual reagendar)
        if pd.notna(row.get('data_hora_sp')):
            try:
                sessao = row['data_hora_sp'].strftime('%d/%m %Hh%M')
            except Exception:
                sessao = '—'
        else:
            sessao = '—'

        # Badge: NAO_VOU é mais urgente que REAGENDAR
        if row.get('tipo') == 'NAO_VOU':
            tipo_badge = '<span class="badge-alerta">🚫 Não vai</span>'
        else:
            tipo_badge = '<span class="badge-amber">🔄 Reagendar</span>'

        c1.markdown(f"<div style='font-weight: 600;'>{nome}</div>{tipo_badge}", unsafe_allow_html=True)
        c2.markdown(f"<div style='font-size: 12px; color: #6B7280;'>+{tel}</div>", unsafe_allow_html=True)
        c3.write(unid)
        c4.markdown(f"<div style='font-size: 12px; color: #4B5563;'>{sessao}</div>", unsafe_allow_html=True)
        c5.markdown(f"<div style='font-size: 12px; color: #6B7280;'>{area}</div>", unsafe_allow_html=True)
        c6.markdown(f"<div style='font-size: 12px; color: #9CA3AF;'>{quando_str}</div>", unsafe_allow_html=True)

        with c7:
            sub_wa, sub_ok = st.columns([1, 1])
            with sub_wa:
                st.link_button("💬 WA", url=f"https://wa.me/{tel}", use_container_width=True)
            with sub_ok:
                if st.button("✓", key=f"contat_ag_{row['id']}", help="Marcar contatado", use_container_width=True):
                    if _marcar_contatado("agendamentos", row['id']):
                        st.toast(f"✅ {nome} marcado como contatado", icon="✅")
                        st.rerun()

    if len(df_f) > 100:
        st.caption(f"Mostrando 100 de {len(df_f)}. Use os filtros pra refinar.")


# ============================================================================
# ENTRYPOINT — chamado de dashboard_maislaser.py
# ============================================================================

def render_aba_pendencias():
    """Render da aba ⚠️ Pendências Bia. Sem args — usa _get_sb() interno."""
    st.markdown("## ⚠️ Pendências Bia")
    st.caption(
        "Casos abertos esperando contato da recepção. "
        "Quando você ligar/atender, marque com ✓ pra remover da fila."
    )

    # Carga das 2 fontes
    df_bd = _carregar_pendencias_bia_disparos()
    df_ag = _carregar_pendencias_agendamentos()

    # Contagens pra badges das sub-abas
    qtd_handoff = int((df_bd['tipo'] == 'HANDOFF').sum()) if not df_bd.empty else 0
    qtd_med     = int((df_bd['tipo'] == 'HANDOFF_MED').sum()) if not df_bd.empty else 0
    qtd_reag    = len(df_ag)
    qtd_total   = qtd_handoff + qtd_med + qtd_reag

    if qtd_total == 0:
        st.success("🎉 **Nenhuma pendência aberta!** A recepção tá em dia.")
        st.caption(
            "Quando aparecer cliente HANDOFF, HANDOFF_MED ou REAGENDAR, "
            "vai listar aqui automaticamente. Atualiza a cada 30s."
        )
        return

    # KPIs no topo
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.markdown(_render_metric_card_local("📋", qtd_total, "Total aberto", "primary"), unsafe_allow_html=True)
    col_m2.markdown(_render_metric_card_local("💬", qtd_handoff, "HANDOFF", "blue"), unsafe_allow_html=True)
    col_m3.markdown(_render_metric_card_local("🩺", qtd_med, "HANDOFF Médico", "purple"), unsafe_allow_html=True)
    col_m4.markdown(_render_metric_card_local("🔄", qtd_reag, "Reagendar / Não vai", "amber"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Sub-abas com contadores no título
    tab_h, tab_m, tab_r = st.tabs([
        f"💬 HANDOFF ({qtd_handoff})",
        f"🩺 HANDOFF Médico ({qtd_med})",
        f"🔄 Reagendar / Não vai ({qtd_reag})",
    ])

    with tab_h:
        df_h = df_bd[df_bd['tipo'] == 'HANDOFF'].copy() if not df_bd.empty else pd.DataFrame()
        _render_lista_handoffs(df_h, "HANDOFF", "pend_h")

    with tab_m:
        df_m = df_bd[df_bd['tipo'] == 'HANDOFF_MED'].copy() if not df_bd.empty else pd.DataFrame()
        _render_lista_handoffs(df_m, "caso médico", "pend_m")

    with tab_r:
        _render_lista_reagendar(df_ag, "pend_r")
