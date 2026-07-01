"""
==============================================================================
ABA CONVERSAS — Timeline reconstruída das interações Bia ↔ Cliente
==============================================================================

Objetivo: acompanhar as conversas em tempo real pra:
  • Ver comportamento real dos clientes
  • Refinar palavras-chave de detecção de intenção
  • Detectar padrões de auto-reply de whatsapp business
  • Analisar taxa de resposta a R1/R2

Fonte: SÓ bia_disparos (não tem tabela de mensagens histórica). A timeline é
RECONSTRUÍDA a partir dos timestamps das colunas.

Colunas usadas:
  • disparado_em          → Bia enviou cortesia_v1 (template)
  • ultima_msg_cliente    → Texto exato da última msg do cliente
  • texto_livre_avisado_em → Momento em que Bia respondeu texto livre
  • tipo_resposta         → Determina qual foi a resposta (ops/despedida)
  • respondeu_em          → Cliente clicou botão ou texto positivo detectado
  • botao_clicado         → Qual botão (AGENDAR/SABER_MAIS)
  • ultima_notif_recepcao → Momento que recepção foi avisada
  • reminder_1_enviado_em → R1
  • reminder_2_enviado_em → R2

==============================================================================
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# SUPABASE lazy import
# ============================================================================

def _get_sb():
    from dashboard_maislaser import get_supabase
    return get_supabase()


# ============================================================================
# CARGA DE DADOS
# ============================================================================

@st.cache_data(ttl=20, show_spinner=False)
def _carregar_conversas(dias_atras=30):
    """Carrega leads que tiveram alguma interação (respondeu OU recebeu ops)."""
    sb = _get_sb()
    try:
        data_limite = (datetime.now(TZ_SP) - timedelta(days=dias_atras)).isoformat()
        # Só leads que já foram disparados (senão não tem conversa)
        result = (sb.table("bia_disparos")
                  .select("id, telefone, nome_indicado, nome_cadastrante, "
                          "campanha_id, telefone_cadastrante, unidade, privacidade, "
                          "status, botao_clicado, fila_em, disparado_em, "
                          "respondeu_em, ultima_notif_recepcao, "
                          "tipo_resposta, texto_livre_avisado_em, ultima_msg_cliente, "
                          "reminder_1_enviado_em, reminder_2_enviado_em")
                  .gte("disparado_em", data_limite)
                  .not_.is_("disparado_em", "null")
                  .order("disparado_em", desc=True)
                  .limit(2000)
                  .execute())
        df = pd.DataFrame(result.data or [])
        if df.empty:
            return df

        # Converte timestamps
        for col in ('fila_em', 'disparado_em', 'respondeu_em', 'ultima_notif_recepcao',
                    'texto_livre_avisado_em', 'reminder_1_enviado_em', 'reminder_2_enviado_em'):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)
                try:
                    df[col + '_sp'] = df[col].dt.tz_convert(TZ_SP)
                except Exception:
                    df[col + '_sp'] = df[col]

        # Calcula "última interação" pra ordenação
        interacao_cols = ['respondeu_em', 'texto_livre_avisado_em',
                          'reminder_2_enviado_em', 'reminder_1_enviado_em',
                          'disparado_em']
        interacao_cols_exist = [c for c in interacao_cols if c in df.columns]
        if interacao_cols_exist:
            df['ultima_interacao'] = df[interacao_cols_exist].max(axis=1)
        else:
            df['ultima_interacao'] = df.get('disparado_em')

        return df
    except Exception as e:
        st.error(f"Erro ao carregar conversas: {e}")
        return pd.DataFrame()


# ============================================================================
# HELPERS
# ============================================================================

def _norm_unidade(u):
    if not isinstance(u, str) or not u.strip():
        return '—'
    ul = u.lower()
    if 'mogi' in ul:
        return 'Mogi'
    if 'suzano' in ul:
        return 'Suzano'
    return u


def _tempo_relativo(ts):
    if pd.isna(ts):
        return "—"
    try:
        delta = pd.Timestamp.now(tz=TZ_SP) - ts
        secs = delta.total_seconds()
        if secs < 60:
            return "agora"
        if secs < 3600:
            return f"{int(secs / 60)}min atrás"
        if secs < 86400:
            return f"{int(secs / 3600)}h atrás"
        return ts.strftime('%d/%m %H:%M')
    except Exception:
        return "—"


def _badge_decisao(tipo_resp):
    """Badge colorido pra decisão do lead."""
    if tipo_resp in ('POSITIVA_BOTAO', 'POSITIVA_TEXTO'):
        cor_bg, cor_fg = '#dcfce7', '#166534'
        label = '✅ Positiva' + (' (botão)' if tipo_resp == 'POSITIVA_BOTAO' else ' (texto)')
    elif tipo_resp == 'NEGATIVA':
        cor_bg, cor_fg = '#fee2e2', '#991b1b'
        label = '❌ Negativa'
    elif tipo_resp == 'GENERICA':
        cor_bg, cor_fg = '#fef3c7', '#92400e'
        label = '💬 Genérica'
    else:
        cor_bg, cor_fg = '#f3f4f6', '#4b5563'
        label = '⏳ Sem resposta'
    return (
        f'<span style="background: {cor_bg}; color: {cor_fg}; '
        f'padding: 3px 10px; border-radius: 12px; font-weight: 700; '
        f'font-size: 11px;">{label}</span>'
    )


def _filtro_unidade():
    key = "_conv_unidade_persist"
    if key not in st.session_state:
        st.session_state[key] = "Todas"
    atual = st.session_state[key]
    st.markdown(
        "<div style='font-size: 14px; color: #6B7280; font-weight: 600; margin-bottom: 6px;'>"
        "📍 Filtrar por unidade</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    for col, label, valor in [(c1, "📌 Todas", "Todas"),
                              (c2, "📍 Mogi", "Mogi"),
                              (c3, "📍 Suzano", "Suzano")]:
        with col:
            if st.button(label, key=f"conv_unid_{valor}",
                         type="primary" if atual == valor else "secondary",
                         use_container_width=True):
                st.session_state[key] = valor
                st.rerun()
    return st.session_state[key]


def _aplicar_filtro_unidade(df, unidade_sel):
    if df is None or df.empty or unidade_sel == 'Todas' or 'unidade' not in df.columns:
        return df
    target = unidade_sel.lower()
    return df[df['unidade'].astype(str).str.lower().str.contains(target, na=False)].copy()


# ============================================================================
# TIMELINE — reconstrói eventos ordenados a partir dos timestamps
# ============================================================================

def _construir_timeline(row):
    """Retorna lista de eventos [{ts, tipo, ator, texto}] ordenados por timestamp."""
    eventos = []

    # 1. Bia enviou template cortesia_v1
    disp = row.get('disparado_em_sp')
    if pd.notna(disp):
        nome_cad = row.get('nome_cadastrante') or 'um amigo'
        unidade = _norm_unidade(row.get('unidade'))
        eventos.append({
            'ts': disp, 'ator': 'bia',
            'tipo': '🚀 Template cortesia_v1',
            'texto': f'Você foi presenteada! Cortesia de {nome_cad} na Maislaser {unidade}. '
                     f'Botões: [AGENDAR] [SABER MAIS]'
        })

    # 2. Cliente enviou texto livre — usa texto_livre_avisado_em como proxy pra momento
    #    (o texto foi recebido ~1s antes do sistema responder)
    tla = row.get('texto_livre_avisado_em_sp')
    if pd.notna(tla):
        texto_cliente = row.get('ultima_msg_cliente') or '(texto não gravado)'
        # Momento aproximado: alguns segundos antes de texto_livre_avisado_em
        eventos.append({
            'ts': tla - pd.Timedelta(seconds=1), 'ator': 'cliente',
            'tipo': '💬 Enviou texto livre',
            'texto': f'"{texto_cliente}"'
        })
        # 3. Bia respondeu (ops/despedida)
        tipo_r = row.get('tipo_resposta')
        if tipo_r == 'NEGATIVA':
            resposta = 'Tudo bem! 😊 Se mudar de ideia, é só voltar aqui.'
            tipo_ev = '👋 Bia respondeu: despedida'
        elif tipo_r == 'GENERICA':
            resposta = 'Ops, não entendi! 😊 Pra eu te ajudar, clica em um dos botões...'
            tipo_ev = '🤖 Bia respondeu: pediu clique'
        else:
            resposta = None
            tipo_ev = None
        if resposta:
            eventos.append({
                'ts': tla, 'ator': 'bia',
                'tipo': tipo_ev,
                'texto': resposta
            })

    # 4. R1 enviado
    r1 = row.get('reminder_1_enviado_em_sp')
    if pd.notna(r1):
        primeiro_nome = str(row.get('nome_indicado') or 'amiga').split()[0]
        unidade = _norm_unidade(row.get('unidade'))
        eventos.append({
            'ts': r1, 'ator': 'bia',
            'tipo': '⏰ Reminder R1',
            'texto': f'Oi {primeiro_nome}! Voltei aqui pra confirmar — você tem interesse '
                     f'no seu presente das 5 sessões grátis na Maislaser {unidade}?'
        })

    # 5. R2 enviado
    r2 = row.get('reminder_2_enviado_em_sp')
    if pd.notna(r2):
        primeiro_nome = str(row.get('nome_indicado') or 'amiga').split()[0]
        eventos.append({
            'ts': r2, 'ator': 'bia',
            'tipo': '⏰ Reminder R2 (último aviso)',
            'texto': f'{primeiro_nome}, último aviso! 🎁 Seu presente vence em breve.'
        })

    # 6. Cliente clicou botão OU texto detectado como positivo
    resp = row.get('respondeu_em_sp')
    if pd.notna(resp):
        botao = row.get('botao_clicado') or '(botão desconhecido)'
        tipo_r = row.get('tipo_resposta')
        if tipo_r == 'POSITIVA_TEXTO':
            eventos.append({
                'ts': resp, 'ator': 'sistema',
                'tipo': f'🎯 Texto detectado como {botao}',
                'texto': f'Sistema interpretou "{row.get("ultima_msg_cliente") or "?"}" como {botao}'
            })
        else:
            eventos.append({
                'ts': resp, 'ator': 'cliente',
                'tipo': f'👆 Clicou botão {botao}',
                'texto': f'Cliente clicou em [{botao}]'
            })
        # Bia respondeu confirmação
        eventos.append({
            'ts': resp + pd.Timedelta(seconds=1), 'ator': 'bia',
            'tipo': '💚 Bia respondeu: confirmação',
            'texto': 'Muito obrigado! 😊 Nossa recepção já vai entrar em contato com você.'
        })

    # 7. Recepção avisada
    notif = row.get('ultima_notif_recepcao_sp')
    if pd.notna(notif):
        unidade = _norm_unidade(row.get('unidade'))
        eventos.append({
            'ts': notif, 'ator': 'sistema',
            'tipo': f'📞 Recepção {unidade} avisada',
            'texto': 'Alerta Z-API enviado pra recepção com link wa.me clicável'
        })

    # Ordena por timestamp
    eventos = [e for e in eventos if pd.notna(e['ts'])]
    eventos.sort(key=lambda e: e['ts'])
    return eventos


def _render_timeline_evento(ev, prev_ts=None):
    """Renderiza 1 evento da timeline como card estilo mensagem de chat."""
    ator = ev['ator']
    if ator == 'bia':
        cor_bg, cor_border, align, emoji_ator = '#e0f2fe', '#0ea5e9', 'left', '🤖'
        label_ator = 'Bia'
    elif ator == 'cliente':
        cor_bg, cor_border, align, emoji_ator = '#dcfce7', '#22c55e', 'right', '👤'
        label_ator = 'Cliente'
    else:
        cor_bg, cor_border, align, emoji_ator = '#f3f4f6', '#9ca3af', 'center', '⚙️'
        label_ator = 'Sistema'

    ts = ev['ts']
    hora = ts.strftime('%H:%M:%S') if pd.notna(ts) else '—'
    data = ts.strftime('%d/%m/%Y') if pd.notna(ts) else '—'

    # Gap desde evento anterior
    gap_html = ''
    if prev_ts is not None and pd.notna(prev_ts) and pd.notna(ts):
        gap_secs = (ts - prev_ts).total_seconds()
        if gap_secs > 60:
            if gap_secs < 3600:
                gap_str = f'{int(gap_secs / 60)}min depois'
            elif gap_secs < 86400:
                gap_str = f'{gap_secs / 3600:.1f}h depois'
            else:
                gap_str = f'{gap_secs / 86400:.1f}d depois'
            gap_html = (
                f'<div style="text-align: center; color: #9ca3af; font-size: 11px; '
                f'margin: 8px 0 4px 0;">⏱️ {gap_str}</div>'
            )

    st.markdown(gap_html + (
        f'<div style="display: flex; justify-content: {align}; margin: 6px 0;">'
        f'<div style="max-width: 75%; background: {cor_bg}; border-left: 3px solid {cor_border}; '
        f'padding: 8px 12px; border-radius: 8px;">'
        f'<div style="font-size: 11px; color: #4b5563; margin-bottom: 4px;">'
        f'{emoji_ator} <strong>{label_ator}</strong> · {data} {hora}'
        f'</div>'
        f'<div style="font-weight: 600; color: #111827; font-size: 13px; margin-bottom: 3px;">'
        f'{ev["tipo"]}'
        f'</div>'
        f'<div style="color: #374151; font-size: 13px; white-space: pre-wrap;">'
        f'{ev["texto"]}'
        f'</div>'
        f'</div>'
        f'</div>'
    ), unsafe_allow_html=True)


# ============================================================================
# DRILL-DOWN — timeline de 1 conversa
# ============================================================================

def _render_drilldown(lead_id, df):
    if st.button("← Voltar pra lista", key="conv_back"):
        st.session_state.pop('_conv_drill_id', None)
        st.rerun()

    linha = df[df['id'] == lead_id]
    if linha.empty:
        st.warning("Conversa não encontrada.")
        return

    row = linha.iloc[0]
    nome = row.get('nome_indicado') or '(sem nome)'
    telefone = row.get('telefone') or '—'
    unidade = _norm_unidade(row.get('unidade'))
    nome_cad = row.get('nome_cadastrante') or '—'

    # Header
    st.markdown(f"## 💬 Conversa — {nome}")
    st.caption(
        f"📱 +{telefone} · 📍 {unidade} · "
        f"👥 Indicado por {nome_cad} · "
        f"🆔 `{row.get('campanha_id', '?')}`"
    )

    # Badge decisão + status
    status = row.get('status') or '?'
    tipo_r = row.get('tipo_resposta')
    st.markdown(
        f'<div style="margin: 10px 0;">'
        f'<span style="background: #f3f4f6; color: #4b5563; padding: 3px 10px; '
        f'border-radius: 12px; font-weight: 700; font-size: 11px; margin-right: 8px;">'
        f'Status: {status}</span>'
        f'{_badge_decisao(tipo_r)}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.divider()

    # Timeline
    eventos = _construir_timeline(row)
    if not eventos:
        st.info("Nenhum evento registrado nessa conversa ainda.")
        return

    st.markdown("### 🕒 Timeline")
    st.caption(f"{len(eventos)} evento(s) reconstruídos a partir dos timestamps do banco.")

    prev_ts = None
    for ev in eventos:
        _render_timeline_evento(ev, prev_ts=prev_ts)
        prev_ts = ev['ts']


# ============================================================================
# LISTA — cards compactos das conversas
# ============================================================================

def _render_lista(df):
    if df.empty:
        st.info("📭 Nenhuma conversa nos últimos 30 dias.")
        return

    st.caption(f"📋 {len(df)} conversa(s)")

    st.markdown("""
    <style>
    .conv-card {
        padding: 12px 16px;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        margin-bottom: 8px;
        background: white;
        transition: all 0.15s ease;
    }
    .conv-card:hover { border-color: #5BC0BE; box-shadow: 0 2px 8px rgba(91,192,190,0.15); }
    </style>
    """, unsafe_allow_html=True)

    # Paginação simples
    ITEMS = 15
    if "_conv_pagina" not in st.session_state:
        st.session_state["_conv_pagina"] = 1
    total_pag = max(1, (len(df) + ITEMS - 1) // ITEMS)
    if st.session_state["_conv_pagina"] > total_pag:
        st.session_state["_conv_pagina"] = 1
    pag = st.session_state["_conv_pagina"]
    ini, fim = (pag - 1) * ITEMS, pag * ITEMS
    df_pag = df.iloc[ini:fim]

    for _, row in df_pag.iterrows():
        nome = row.get('nome_indicado') or '(sem nome)'
        telefone = row.get('telefone') or ''
        unidade = _norm_unidade(row.get('unidade'))
        nome_cad = row.get('nome_cadastrante') or '—'
        tipo_r = row.get('tipo_resposta')
        ultima_msg = row.get('ultima_msg_cliente')
        ultima_int = row.get('ultima_interacao')
        try:
            ultima_int_sp = pd.to_datetime(ultima_int).tz_convert(TZ_SP)
        except Exception:
            ultima_int_sp = ultima_int
        quando = _tempo_relativo(ultima_int_sp)

        preview = ''
        if ultima_msg and isinstance(ultima_msg, str) and ultima_msg.strip():
            texto = ultima_msg.strip()[:80]
            if len(ultima_msg) > 80:
                texto += '…'
            preview = (
                f'<div style="margin-top: 6px; color: #6b7280; font-size: 12px; '
                f'font-style: italic;">💬 "{texto}"</div>'
            )

        html = (
            f'<div class="conv-card">'
            f'<div style="display: flex; justify-content: space-between; align-items: flex-start;">'
            f'<div>'
            f'<strong style="font-size: 14px;">{nome}</strong>'
            f'<span style="color: #6b7280; font-size: 12px;"> · 📱 +{telefone} · 📍 {unidade}</span>'
            f'<div style="color: #6b7280; font-size: 11px; margin-top: 2px;">'
            f'Indicado por {nome_cad} · 🕒 {quando}'
            f'</div>'
            f'{preview}'
            f'</div>'
            f'<div>{_badge_decisao(tipo_r)}</div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)

        # Link HTML pra abrir conversa em NOVA ABA do navegador.
        # Preserva o token de auth (?t=...) e passa ?conv_id=X.
        # O dashboard_maislaser.py detecta conv_id na URL e renderiza fullscreen.
        try:
            qp_atuais = st.query_params
            t_atual = qp_atuais.get("t", "")
        except Exception:
            t_atual = ""
        url_conv = f"?conv_id={row['id']}"
        if t_atual:
            url_conv += f"&t={t_atual}"

        st.markdown(
            f'<div style="margin-top: 6px;">'
            f'<a href="{url_conv}" target="_blank" '
            f'style="display: inline-block; padding: 6px 14px; '
            f'background: white; color: #3D9991; border: 1px solid #E5E7EB; '
            f'border-radius: 8px; text-decoration: none; font-weight: 600; '
            f'font-size: 13px; transition: all 0.15s ease;">'
            f'🔍 Ver conversa <span style="color: #9ca3af;">↗</span>'
            f'</a>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Controles paginação
    if total_pag > 1:
        st.markdown("<br>", unsafe_allow_html=True)
        cp, ci, cn = st.columns([1, 2, 1])
        with cp:
            if st.button("← Anterior", key="conv_prev",
                         disabled=(pag == 1), use_container_width=True):
                st.session_state["_conv_pagina"] -= 1
                st.rerun()
        with ci:
            st.markdown(
                f"<div style='text-align: center; padding-top: 6px; color: #6b7280;'>"
                f"Página <strong>{pag}</strong> de <strong>{total_pag}</strong> "
                f"· mostrando {ini + 1}–{min(fim, len(df))} de {len(df)}</div>",
                unsafe_allow_html=True,
            )
        with cn:
            if st.button("Próxima →", key="conv_next",
                         disabled=(pag == total_pag), use_container_width=True):
                st.session_state["_conv_pagina"] += 1
                st.rerun()


# ============================================================================
# ENTRYPOINT
# ============================================================================

def render_aba_conversas():
    st.markdown("## 💬 Conversas")
    st.caption(
        "Timeline reconstruída das interações Bia ↔ Cliente. Útil pra ver "
        "comportamento real e refinar palavras-chave da v3.3+."
    )

    unidade_sel = _filtro_unidade()
    st.markdown(
        '<hr style="margin: 12px 0 18px 0; border: none; border-top: 1px solid #E5E7EB;">',
        unsafe_allow_html=True,
    )

    df = _carregar_conversas(dias_atras=30)
    if df.empty:
        st.info("📭 Nenhuma conversa nos últimos 30 dias.")
        return

    df_f = _aplicar_filtro_unidade(df, unidade_sel)

    # Drill-down ativo? (via session_state pra navegação inline)
    drill_id = st.session_state.get('_conv_drill_id')
    if drill_id:
        _render_drilldown(drill_id, df_f)
    else:
        _render_lista(df_f)


# ============================================================================
# FULLSCREEN — chamado pelo dashboard_maislaser.py quando URL tem ?conv_id=xxx
# ============================================================================

def render_conversa_fullscreen(conv_id):
    """Renderiza SÓ a timeline de 1 conversa, sem sidebar/abas.
    Usado quando o usuário abre '?conv_id=xxx' em nova aba do browser."""

    # Botão voltar (limpa query params e recarrega dashboard normal)
    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("🏠 Voltar pro dashboard", use_container_width=True):
            # Preserva só o token de auth
            qp = st.query_params
            t_atual = qp.get("t", None)
            st.query_params.clear()
            if t_atual:
                st.query_params["t"] = t_atual
            st.rerun()

    st.markdown(
        '<hr style="margin: 12px 0 18px 0; border: none; border-top: 1px solid #E5E7EB;">',
        unsafe_allow_html=True,
    )

    # Carrega o lead específico direto do Supabase
    try:
        sb = _get_sb()
        result = (sb.table("bia_disparos")
                  .select("id, telefone, nome_indicado, nome_cadastrante, "
                          "campanha_id, telefone_cadastrante, unidade, privacidade, "
                          "status, botao_clicado, fila_em, disparado_em, "
                          "respondeu_em, ultima_notif_recepcao, "
                          "tipo_resposta, texto_livre_avisado_em, ultima_msg_cliente, "
                          "reminder_1_enviado_em, reminder_2_enviado_em")
                  .eq("id", conv_id)
                  .limit(1)
                  .execute())
        if not result.data:
            st.error(f"❌ Conversa `{conv_id}` não encontrada.")
            return
        df = pd.DataFrame(result.data)
        # Converte timestamps
        for col in ('fila_em', 'disparado_em', 'respondeu_em', 'ultima_notif_recepcao',
                    'texto_livre_avisado_em', 'reminder_1_enviado_em', 'reminder_2_enviado_em'):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)
                try:
                    df[col + '_sp'] = df[col].dt.tz_convert(TZ_SP)
                except Exception:
                    df[col + '_sp'] = df[col]
    except Exception as e:
        st.error(f"Erro ao carregar conversa: {e}")
        return

    # Reusa o drill-down normal (mesma UI, sem botão de voltar interno)
    _render_drilldown_fullscreen(conv_id, df)


def _render_drilldown_fullscreen(lead_id, df):
    """Igual ao _render_drilldown mas SEM o botão 'Voltar pra lista' interno
    (já tem o 'Voltar pro dashboard' no fullscreen)."""
    linha = df[df['id'] == lead_id]
    if linha.empty:
        st.warning("Conversa não encontrada.")
        return

    row = linha.iloc[0]
    nome = row.get('nome_indicado') or '(sem nome)'
    telefone = row.get('telefone') or '—'
    unidade = _norm_unidade(row.get('unidade'))
    nome_cad = row.get('nome_cadastrante') or '—'

    st.markdown(f"## 💬 Conversa — {nome}")
    st.caption(
        f"📱 +{telefone} · 📍 {unidade} · "
        f"👥 Indicado por {nome_cad} · "
        f"🆔 `{row.get('campanha_id', '?')}`"
    )

    status = row.get('status') or '?'
    tipo_r = row.get('tipo_resposta')
    st.markdown(
        f'<div style="margin: 10px 0;">'
        f'<span style="background: #f3f4f6; color: #4b5563; padding: 3px 10px; '
        f'border-radius: 12px; font-weight: 700; font-size: 11px; margin-right: 8px;">'
        f'Status: {status}</span>'
        f'{_badge_decisao(tipo_r)}'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.divider()

    eventos = _construir_timeline(row)
    if not eventos:
        st.info("Nenhum evento registrado nessa conversa ainda.")
        return

    st.markdown("### 🕒 Timeline")
    st.caption(f"{len(eventos)} evento(s) reconstruídos a partir dos timestamps do banco.")

    prev_ts = None
    for ev in eventos:
        _render_timeline_evento(ev, prev_ts=prev_ts)
        prev_ts = ev['ts']
