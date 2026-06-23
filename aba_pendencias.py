"""
==============================================================================
ABA PENDÊNCIAS BIA — Casos abertos esperando contato da recepção
==============================================================================
Substitui a notificação WhatsApp avulsa (que sofria com janela 24h da Meta)
por um painel persistente onde a recepção entra e vê o que precisa atender.

Lê 3 fontes do Supabase:
  • bia_disparos      → status IN ['HANDOFF', 'HANDOFF_MED', 'HANDOFF_MEDICO']
  • agendamentos      → reagendamento_solicitado_em OR nao_vou_conseguir_em
  • bia_disparos      → status='AGENDADA' com pergunta pós-agendamento
                        (ultima_notif_recepcao > desfecho_em)

Todas filtradas por contatado_em IS NULL (pendência aberta), EXCETO a de
pós-agendamento que usa lógica especial: aparece se cliente voltou a perguntar
DEPOIS do contato (ultima_notif_recepcao > contatado_em).

Ação principal: botão "✓ Marcar contatado" grava timestamp em contatado_em.
Não muda o status original (preserva audit trail: "isso foi um HANDOFF_MED
que foi atendido em XX/YY").

Sub-abas:
  💬 HANDOFF              — cliente confuso / preço / fora do script
  🩺 HANDOFF Médico       — triagem médica positiva
  🔄 Reagendar/Não vai    — clicou PRECISO REAGENDAR ou NÃO VOU CONSEGUIR
  💭 Pergunta pós-agend.  — AGENDADA com dúvida (preparo, local, etc)

v3.0 (23/06/2026):
  - Filtro GLOBAL de unidade (Todas/Mogi/Suzano) movido pro topo da aba
  - Cards e sub-abas refletem o filtro de unidade selecionado
  - Filtro de unidade duplicado dentro das sub-abas REMOVIDO
  - Dentro de cada sub-aba sobra só o filtro de PERÍODO
  - Seleção da unidade persiste via session_state (_pend_unidade_persist)
==============================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone

# Reusa helpers já existentes (métricas, export) — mesma UX do resto do app
# NOTA v3.0: removemos a importação de `_filtros_periodo_unidade` porque agora
# usamos `_filtro_periodo_local` definido aqui (sem filtro de unidade, já que
# isso virou global no topo).
from aba_confirmacao import (
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
# CARGAS DE DADOS — 3 queries no Supabase com cache 30s
# ============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def _carregar_pendencias_bia_disparos():
    """HANDOFF + HANDOFF_MED + HANDOFF_MEDICO pendentes (contatado_em IS NULL)."""
    sb = _get_sb()
    try:
        result = sb.table("bia_disparos").select(
            "id, telefone, nome_indicado, nome_cadastrante, unidade, status, "
            "desfecho_em, ultima_notif_recepcao, fila_em, disparado_em"
        ).in_("status", ["HANDOFF", "HANDOFF_MED", "HANDOFF_MEDICO"]) \
         .is_("contatado_em", "null") \
         .order("desfecho_em", desc=True) \
         .limit(500).execute()

        df = pd.DataFrame(result.data)
        if df.empty:
            return df

        # Parse timestamps tz-aware
        for col in ('desfecho_em', 'ultima_notif_recepcao', 'fila_em', 'disparado_em'):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)

        # Coluna unificada "quando virou pendência" (com fallback)
        df['quando'] = df['desfecho_em'].fillna(df['fila_em']).fillna(df['disparado_em'])
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


@st.cache_data(ttl=30, show_spinner=False)
def _carregar_pendencias_pos_agendamento():
    """AGENDADA com pergunta pós-agendamento ainda não respondida pela recepção.

    Filtro: cliente já agendou (desfecho_em preenchido), depois mandou msg que
    disparou notif (ultima_notif_recepcao > desfecho_em), e recepção não marcou
    contatado_em OU cliente voltou a mandar msg depois do contato 
    (ultima_notif_recepcao > contatado_em)."""
    sb = _get_sb()
    try:
        # Carrega todos AGENDADA com ultima_notif_recepcao preenchida
        result = sb.table("bia_disparos").select(
            "id, telefone, nome_indicado, nome_cadastrante, unidade, status, "
            "desfecho_em, ultima_notif_recepcao, contatado_em, slot_area, slot_dia, slot_hora"
        ).eq("status", "AGENDADA") \
         .not_.is_("ultima_notif_recepcao", "null") \
         .limit(500).execute()

        df = pd.DataFrame(result.data)
        if df.empty:
            return df

        # Parse timestamps tz-aware
        for col in ('desfecho_em', 'ultima_notif_recepcao', 'contatado_em', 'slot_dia'):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)

        # Filtro Python: msg veio DEPOIS do agendamento (pergunta pós-fechamento)
        df = df[df['ultima_notif_recepcao'] > df['desfecho_em']].copy()
        if df.empty:
            return df

        # Pendente se: contatado_em null OU cliente voltou (ultima_notif > contatado)
        mask_pendente = df['contatado_em'].isna() | (df['ultima_notif_recepcao'] > df['contatado_em'])
        df = df[mask_pendente].copy()
        if df.empty:
            return df

        # Coluna "quando virou pendência" = ultima_notif_recepcao
        try:
            df['quando_sp'] = df['ultima_notif_recepcao'].dt.tz_convert(TZ_SP)
            df['sessao_sp'] = df['slot_dia'].dt.tz_convert(TZ_SP) if 'slot_dia' in df.columns else None
        except Exception:
            df['quando_sp'] = df['ultima_notif_recepcao']
            df['sessao_sp'] = df.get('slot_dia')

        df['nome'] = df['nome_indicado'].fillna('Sem nome')

        return df.sort_values('quando_sp', ascending=False)
    except Exception as e:
        st.error(f"Erro ao carregar perguntas pós-agendamento: {e}")
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
        _carregar_pendencias_pos_agendamento.clear()
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
# v3.0 — FILTROS NOVOS (unidade global + período local)
# ============================================================================

def _filtro_unidade_global():
    """
    Renderiza filtro global de unidade no topo da aba Pendências.
    Retorna: 'Todas' | 'Mogi' | 'Suzano' (persistido em session_state).

    Visual: 3 botões em colunas, o selecionado fica type="primary".
    """
    key_persist = "_pend_unidade_persist"
    if key_persist not in st.session_state:
        st.session_state[key_persist] = "Todas"

    atual = st.session_state[key_persist]

    st.markdown(
        "<div style='font-size: 14px; color: #6B7280; font-weight: 600; margin-bottom: 6px;'>"
        "📍 Filtrar por unidade"
        "</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)

    opcoes = [
        (c1, "📌 Todas",    "Todas"),
        (c2, "📍 Mogi",     "Mogi"),
        (c3, "📍 Suzano",   "Suzano"),
    ]

    for col, label, valor in opcoes:
        with col:
            if st.button(
                label,
                key=f"pend_und_global_{valor}",
                type="primary" if atual == valor else "secondary",
                use_container_width=True,
            ):
                st.session_state[key_persist] = valor
                st.rerun()

    return st.session_state[key_persist]


def _aplicar_filtro_unidade(df, unidade_sel):
    """
    Filtra df pela unidade selecionada. 'Todas' retorna df inteiro.
    Match case-insensitive (pega 'Mogi', 'mogi', 'Mogi das Cruzes', etc).
    """
    if df is None or df.empty or unidade_sel == "Todas":
        return df
    if 'unidade' not in df.columns:
        return df
    target = unidade_sel.lower()
    return df[
        df['unidade'].astype(str).str.lower().str.contains(target, na=False)
    ].copy()


def _filtro_periodo_local(df, coluna_data, key_prefix, default="Tudo"):
    """
    Renderiza só filtro de período (Hoje / 24h / 3 dias / Tudo) e retorna df filtrado.
    Estado persiste em session_state[f'_{key_prefix}_periodo_persist'].

    Substitui o `_filtros_periodo_unidade` antigo de aba_confirmacao — esta
    versão NÃO renderiza filtro de unidade (que virou global no topo da aba).
    """
    if df is None or df.empty:
        return df

    key_persist = f"_{key_prefix}_periodo_persist"
    if key_persist not in st.session_state:
        st.session_state[key_persist] = default

    atual = st.session_state[key_persist]

    c1, c2, c3, c4 = st.columns(4)
    opcoes = [
        (c1, "📅 Hoje",            "Hoje"),
        (c2, "🕐 Últimas 24h",      "Últimas 24h"),
        (c3, "📅 Últimos 3 dias",   "Últimos 3 dias"),
        (c4, "♾️ Tudo",             "Tudo"),
    ]

    for col, label, valor in opcoes:
        with col:
            if st.button(
                label,
                key=f"{key_prefix}_per_{valor}",
                type="primary" if atual == valor else "secondary",
                use_container_width=True,
            ):
                st.session_state[key_persist] = valor
                st.rerun()

    # Aplica corte temporal
    agora = pd.Timestamp.now(tz=TZ_SP)
    try:
        if atual == "Hoje":
            inicio = agora.normalize()
            return df[df[coluna_data] >= inicio].copy()
        elif atual == "Últimas 24h":
            inicio = agora - pd.Timedelta(hours=24)
            return df[df[coluna_data] >= inicio].copy()
        elif atual == "Últimos 3 dias":
            inicio = agora - pd.Timedelta(days=3)
            return df[df[coluna_data] >= inicio].copy()
        else:  # Tudo
            return df
    except Exception:
        # Se o corte falhar (ex: coluna sem tz), retorna df sem filtrar pra não quebrar a aba
        return df


# ============================================================================
# RENDER POR SUB-ABA
# ============================================================================

def _render_lista_handoffs(df, tipo_label, key_prefix):
    """Renderiza lista de HANDOFF ou HANDOFF_MED (vindos de bia_disparos).
    v3.0: usa _filtro_periodo_local (unidade já foi filtrada globalmente)."""
    if df.empty:
        st.success(f"🎉 Nenhum {tipo_label} aberto!")
        return

    df_f = _filtro_periodo_local(df, "quando_sp", key_prefix, default="Tudo")

    if df_f.empty:
        st.info("Nenhum caso no período selecionado.")
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
    """Renderiza lista REAGENDAR + NAO_VOU (vindos de agendamentos).
    v3.0: usa _filtro_periodo_local (unidade já foi filtrada globalmente)."""
    if df.empty:
        st.success("🎉 Nenhuma pendência aberta!")
        return

    df_f = _filtro_periodo_local(df, "quando_sp", key_prefix, default="Tudo")

    if df_f.empty:
        st.info("Nenhum caso no período selecionado.")
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


def _render_lista_pos_agendamento(df, key_prefix):
    """Renderiza lista de AGENDADA com pergunta pós-agendamento.
    v3.0: usa _filtro_periodo_local (unidade já foi filtrada globalmente)."""
    if df.empty:
        st.success("🎉 Nenhuma pergunta pós-agendamento aberta!")
        return

    df_f = _filtro_periodo_local(df, "quando_sp", key_prefix, default="Tudo")

    if df_f.empty:
        st.info("Nenhum caso no período selecionado.")
        return

    col_t, col_e = st.columns([4, 1.4])
    with col_t:
        st.markdown(f"### {len(df_f)} pergunta(s) aberta(s)")
    with col_e:
        _botao_export_xlsx(df_f, "pendencias_pos_agend", key_prefix)

    h1, h2, h3, h4, h5, h6, h7 = st.columns([1.7, 1.2, 0.8, 1.2, 1.0, 0.9, 1.5])
    h1.markdown("**Cliente**")
    h2.markdown("**Telefone**")
    h3.markdown("**Unidade**")
    h4.markdown("**Sessão**")
    h5.markdown("**Área**")
    h6.markdown("**Perguntou há**")
    h7.markdown("**Ação**")
    st.markdown('<hr style="margin: 4px 0 8px 0;">', unsafe_allow_html=True)

    for _, row in df_f.head(100).iterrows():
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.7, 1.2, 0.8, 1.2, 1.0, 0.9, 1.5])

        nome = row.get('nome') or '—'
        tel = str(row.get('telefone', '—'))
        unid = _norm_unidade_display(row.get('unidade'))
        area = row.get('slot_area') or '—'
        quando_str = _tempo_relativo(row.get('quando_sp'))

        # Sessão agendada (slot_dia + slot_hora)
        sessao = '—'
        if pd.notna(row.get('sessao_sp')):
            try:
                hora = row.get('slot_hora') or ''
                hora_str = str(hora)[:5] if hora else ''
                sessao = row['sessao_sp'].strftime('%d/%m') + (f' {hora_str}' if hora_str else '')
            except Exception:
                sessao = '—'

        # Badge: cliente nova vs já contatado mas voltou
        if pd.notna(row.get('contatado_em')):
            tipo_badge = '<span class="badge-amber">🔁 Voltou a perguntar</span>'
        else:
            tipo_badge = '<span class="badge-info">💭 Pergunta nova</span>'

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
                if st.button("✓", key=f"contat_pa_{row['id']}", help="Marcar contatado", use_container_width=True):
                    if _marcar_contatado("bia_disparos", row['id']):
                        st.toast(f"✅ {nome} marcado como contatado", icon="✅")
                        st.rerun()

    if len(df_f) > 100:
        st.caption(f"Mostrando 100 de {len(df_f)}. Use os filtros pra refinar.")


# ============================================================================
# ENTRYPOINT — chamado de dashboard_maislaser.py
# ============================================================================

def render_aba_pendencias():
    """Render da aba ⚠️ Pendências Bia. Sem args — usa _get_sb() interno.

    v3.0 — fluxo:
      1. Título + caption
      2. Filtro GLOBAL de unidade (Todas / Mogi / Suzano)
      3. Cards (refletem filtro de unidade)
      4. Sub-abas (refletem filtro de unidade; só filtro de período interno)
    """
    st.markdown("## ⚠️ Pendências Bia")
    st.caption(
        "Casos abertos esperando contato da recepção. "
        "Quando você ligar/atender, marque com ✓ pra remover da fila."
    )

    # ── v3.0: FILTRO GLOBAL de unidade ANTES dos cards ────────────────────
    unidade_sel = _filtro_unidade_global()
    st.markdown(
        '<hr style="margin: 12px 0 18px 0; border: none; border-top: 1px solid #E5E7EB;">',
        unsafe_allow_html=True,
    )

    # ── Carga das 3 fontes (já em cache de 30s) ───────────────────────────
    df_bd_raw = _carregar_pendencias_bia_disparos()
    df_ag_raw = _carregar_pendencias_agendamentos()
    df_pa_raw = _carregar_pendencias_pos_agendamento()

    # ── Aplica filtro global de unidade nos 3 dfs ─────────────────────────
    df_bd = _aplicar_filtro_unidade(df_bd_raw, unidade_sel)
    df_ag = _aplicar_filtro_unidade(df_ag_raw, unidade_sel)
    df_pa = _aplicar_filtro_unidade(df_pa_raw, unidade_sel)

    # Contagens pra badges das sub-abas (já refletem filtro de unidade)
    qtd_handoff = int((df_bd['tipo'] == 'HANDOFF').sum()) if not df_bd.empty else 0
    qtd_med     = int((df_bd['tipo'] == 'HANDOFF_MED').sum()) if not df_bd.empty else 0
    qtd_reag    = len(df_ag)
    qtd_pos     = len(df_pa)
    qtd_total   = qtd_handoff + qtd_med + qtd_reag + qtd_pos

    if qtd_total == 0:
        # Mensagem diferenciada se filtrou unidade vs total
        if unidade_sel != "Todas":
            st.success(
                f"🎉 **Nenhuma pendência aberta em {unidade_sel}!** Recepção em dia."
            )
            st.caption(
                f"Mostrando apenas {unidade_sel}. Clique em **📌 Todas** acima pra ver tudo."
            )
        else:
            st.success("🎉 **Nenhuma pendência aberta!** A recepção tá em dia.")
            st.caption(
                "Quando aparecer cliente HANDOFF, HANDOFF_MED, REAGENDAR ou pergunta pós-agendamento, "
                "vai listar aqui automaticamente. Atualiza a cada 30s."
            )
        return

    # ── KPIs no topo (5 cards) — refletem filtro de unidade ───────────────
    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    col_m1.markdown(_render_metric_card_local("📋", qtd_total, "Total aberto", "primary"), unsafe_allow_html=True)
    col_m2.markdown(_render_metric_card_local("💬", qtd_handoff, "HANDOFF", "blue"), unsafe_allow_html=True)
    col_m3.markdown(_render_metric_card_local("🩺", qtd_med, "HANDOFF Médico", "purple"), unsafe_allow_html=True)
    col_m4.markdown(_render_metric_card_local("🔄", qtd_reag, "Reagendar / Não vai", "amber"), unsafe_allow_html=True)
    col_m5.markdown(_render_metric_card_local("💭", qtd_pos, "Pergunta pós-agend", "green"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Sub-abas com contadores no título (já refletem filtro de unidade) ─
    tab_h, tab_m, tab_r, tab_p = st.tabs([
        f"💬 HANDOFF ({qtd_handoff})",
        f"🩺 HANDOFF Médico ({qtd_med})",
        f"🔄 Reagendar / Não vai ({qtd_reag})",
        f"💭 Pergunta pós-agendamento ({qtd_pos})",
    ])

    with tab_h:
        df_h = df_bd[df_bd['tipo'] == 'HANDOFF'].copy() if not df_bd.empty else pd.DataFrame()
        _render_lista_handoffs(df_h, "HANDOFF", "pend_h")

    with tab_m:
        df_m = df_bd[df_bd['tipo'] == 'HANDOFF_MED'].copy() if not df_bd.empty else pd.DataFrame()
        _render_lista_handoffs(df_m, "caso médico", "pend_m")

    with tab_r:
        _render_lista_reagendar(df_ag, "pend_r")

    with tab_p:
        _render_lista_pos_agendamento(df_pa, "pend_p")
