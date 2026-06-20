"""
==============================================================================
ABA Z-API INDICAÇÕES — Robô Z-API (Apps Script v9.8)
==============================================================================
Conecta o dashboard aos endpoints do Apps Script do Z-API:

  GET endpoints (leitura):
    /?endpoint=ping              → healthcheck
    /?endpoint=clientes          → todas as linhas de CLIENTES
    /?endpoint=indicacoes&limit  → últimas N indicações
    /?endpoint=validacao         → pendentes de validação enriquecidas
                                    (v9.8: agora retorna modo + bia_puxou_em)
    /?endpoint=contatos_cliente&campanha_id=ID → 20 contatos da campanha
    /?endpoint=funcionarias      → ranking
    /?endpoint=funcionarias_real → ranking calculado em tempo real
    /?endpoint=metricas_funil    → funil completo
    /?endpoint=stats             → métricas agregadas leves
    /?endpoint=get_default_modo  → v9.8: lê toggle bia_default_modo_auto

  AÇÕES (também GET, com query params):
    /?endpoint=marcar_validacao&tel=...&decisao=VALIDADO|INVALIDADO&modo=AUTO|MANUAL
      → marca o dropdown na aba certa. Trigger processarValidacoes (5min)
        dispara voucher / mensagem.
    /?endpoint=set_modo_campanha&tel=...&modo=AUTO|MANUAL  → v9.8
    /?endpoint=set_default_modo&modo=AUTO|MANUAL           → v9.8

v9.8 (18/06/2026): FEATURE MODO MANUAL/AUTO
  • Toggle global "Default modo das próximas campanhas" no topo da aba
  • Card de cada campanha com 4 estados:
      - SEM DECISÃO → botões MANUAL / AUTO
      - MANUAL → botões Validar/Invalidar + opção mudar pra AUTO
      - AUTO (aguardando puxar) → mensagem informativa + mudar pra MANUAL
      - AUTO (Bia rodando) → progresso X/Y + tempo restante, só visualização
  • Progresso lido direto do Supabase (bia_disparos.respondeu_em)
==============================================================================
"""

import streamlit as st
import pandas as pd
import requests
from supabase import create_client
from datetime import datetime, timedelta, timezone, date
from io import BytesIO

TZ_SP = timezone(timedelta(hours=-3))


# ============================================================================
# CLIENTE HTTP — cacheado, com timeout e fallback gracioso
# ============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def _zapi_get(endpoint: str, **params):
    """
    Chama um endpoint do Apps Script do Z-API.
    Cache 30s. Timeout 15s. Se falhar, retorna {'_erro': '...'}.
    """
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente: adicione APPS_SCRIPT_URL_ZAPI e APPS_SCRIPT_TOKEN_ZAPI nos secrets do Streamlit."}

    query = {"endpoint": endpoint, "token": token, **{k: v for k, v in params.items() if v is not None}}
    try:
        resp = requests.get(url, params=query, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code} ao chamar {endpoint}"}
        data = resp.json()
        if isinstance(data, dict) and data.get("erro"):
            return {"_erro": f"Z-API: {data['erro']}"}
        return data
    except requests.exceptions.Timeout:
        return {"_erro": "Apps Script Z-API demorou demais (>20s). Tente atualizar."}
    except requests.exceptions.RequestException as e:
        return {"_erro": f"Erro de rede: {e}"}
    except ValueError:
        return {"_erro": "Resposta do Apps Script não é JSON válido."}


def _zapi_action(endpoint: str, **params):
    """
    Versão NÃO cacheada do _zapi_get, para AÇÕES (marcar_validacao, set_modo_campanha, etc).
    Cache não tem cabimento aqui porque cada clique precisa chegar no Apps Script.
    """
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente: APPS_SCRIPT_URL_ZAPI / APPS_SCRIPT_TOKEN_ZAPI"}

    query = {"endpoint": endpoint, "token": token, **{k: v for k, v in params.items() if v is not None}}
    try:
        resp = requests.get(url, params=query, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code}"}
        return resp.json()
    except Exception as e:
        return {"_erro": f"Erro: {e}"}


def _mostrar_erro_e_parar(data, contexto=""):
    """Helper: se data tem _erro, mostra alert e retorna True (caller deve return)."""
    if isinstance(data, dict) and data.get("_erro"):
        st.error(f"❌ {data['_erro']}" + (f" {contexto}" if contexto else ""))
        return True
    return False


# ============================================================================
# HELPERS DE FORMATAÇÃO
# ============================================================================

def _parse_iso(s):
    """ISO string (qualquer flavor) → datetime tz-aware em SP. None se vazio/inválido."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ_SP)
    except Exception:
        return None


def _humanizar_tempo(dt):
    """Datetime → string tipo '4h', '2d 3h', '1 semana'. None se dt for None."""
    if dt is None:
        return "—"
    agora = datetime.now(TZ_SP)
    delta = agora - dt
    segs = int(delta.total_seconds())
    if segs < 60:
        return "agora"
    if segs < 3600:
        return f"{segs // 60}min"
    if segs < 86400:
        h = segs // 3600
        return f"{h}h"
    dias = segs // 86400
    h = (segs % 86400) // 3600
    if dias < 7:
        return f"{dias}d {h}h" if h else f"{dias}d"
    semanas = dias // 7
    return f"{semanas} sem"


def _classe_urgencia(dt):
    """Datetime → string de urgência ('ok', 'atencao', 'urgente') por idade."""
    if dt is None:
        return "ok"
    agora = datetime.now(TZ_SP)
    horas = (agora - dt).total_seconds() / 3600
    if horas < 12:
        return "ok"
    if horas < 24:
        return "atencao"
    return "urgente"


def _formatar_telefone(tel):
    """5511974869664 → +55 (11) 97486-9664"""
    s = str(tel).strip()
    if s.startswith("55") and len(s) == 13:
        return f"+55 ({s[2:4]}) {s[4:9]}-{s[9:]}"
    return s


# ============================================================================
# v9.8 — HELPERS NOVOS (MODO MANUAL/AUTO)
# ============================================================================

@st.cache_resource
def _get_supabase_zapi():
    """
    Cliente Supabase dedicado pro aba_zapi.py (segue padrão do
    dashboard_maislaser.py: cached_resource, lê de st.secrets).
    """
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


@st.cache_data(ttl=60, show_spinner=False)
def _get_default_modo():
    """
    Lê o toggle global 'bia_default_modo_auto' do Apps Script.
    Cache 60s — invalidado manualmente após _set_default_modo().
    Retorna 'AUTO' ou 'MANUAL' (default 'MANUAL' se erro).
    """
    data = _zapi_get("get_default_modo")
    if isinstance(data, dict) and data.get("_erro"):
        return "MANUAL"  # fallback seguro
    modo = str(data.get("modo", "MANUAL")).upper()
    return modo if modo in ("AUTO", "MANUAL") else "MANUAL"


def _set_default_modo(modo):
    """
    Grava o toggle global no Apps Script + invalida o cache da leitura.
    Retorna True se OK, False se erro.
    """
    resp = _zapi_action("set_default_modo", modo=modo)
    if resp.get("_erro") or resp.get("erro"):
        st.error(f"❌ Falhou: {resp.get('_erro') or resp.get('erro')}")
        return False
    _get_default_modo.clear()
    return True


@st.cache_data(ttl=30, show_spinner=False)
def _get_progresso_campanhas_bia(campanha_ids_tuple):
    """
    Conta respostas no Supabase por campanha (bia_disparos.respondeu_em IS NOT NULL).
    Recebe TUPLA (não lista — pra ser hasheável pro cache do Streamlit).
    Retorna dict {campanha_id: total_respostas}.
    Cache 30s.
    """
    if not campanha_ids_tuple:
        return {}
    try:
        sb = _get_supabase_zapi()
        result = (
            sb.table("bia_disparos")
            .select("campanha_id, respondeu_em")
            .in_("campanha_id", list(campanha_ids_tuple))
            .not_.is_("respondeu_em", "null")
            .execute()
        )
        contagem = {}
        for row in result.data or []:
            cid = row.get("campanha_id")
            if cid:
                contagem[cid] = contagem.get(cid, 0) + 1
        return contagem
    except Exception as e:
        # Falha silenciosa — UI mostra "—" no progresso
        st.toast(f"⚠️ Não consegui ler progresso Bia: {e}", icon="⚠️")
        return {}


def _meta_respostas(total_contatos):
    """30% arredondado pra cima. Ex: 20 → 6, 24 → 8, 82 → 25."""
    import math
    return max(1, math.ceil(0.3 * int(total_contatos or 0)))


# ============================================================================
# TELA: ⏳ AGUARDANDO VALIDAÇÃO (v9.8 — feature MODO MANUAL/AUTO)
# ============================================================================

def tela_zapi_aguardando_validacao():
    st.markdown("## ⏳ Aguardando validação")
    st.caption(
        "Coordenadora decide o **MODO** de cada campanha:  "
        "**👤 MANUAL** = captadora liga e valida.  "
        "**🤖 AUTO** = Bia v5 puxa o lote e dispara templates pros indicados; "
        "valida sozinha ao atingir 30% de respostas em até 36h."
    )

    # ───────────────────────────────────────────────────────────────────
    # TOGGLE GLOBAL — Default das próximas campanhas
    # ───────────────────────────────────────────────────────────────────
    modo_default_atual = _get_default_modo()

    with st.container():
        st.markdown(
            """
            <style>
            .toggle-global-box {
                background: linear-gradient(135deg, #f0f9ff 0%, #ecfeff 100%);
                border: 1px solid #bae6fd;
                border-radius: 12px;
                padding: 14px 18px;
                margin-bottom: 16px;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.markdown('<div class="toggle-global-box">', unsafe_allow_html=True)

        col_lbl, col_radio, _ = st.columns([3, 4, 1])
        with col_lbl:
            st.markdown(
                "**🎛️ Modo padrão das próximas campanhas**  \n"
                "<small>Vale só pra **visualização** — coordenadora decide cada uma abaixo.</small>",
                unsafe_allow_html=True,
            )
        with col_radio:
            modo_novo = st.radio(
                "Modo padrão",
                ["MANUAL", "AUTO"],
                index=0 if modo_default_atual == "MANUAL" else 1,
                horizontal=True,
                key="toggle_default_modo",
                label_visibility="collapsed",
                format_func=lambda x: f"👤 {x} (captadora liga)" if x == "MANUAL" else f"🤖 {x} (Bia trabalha)",
            )
            if modo_novo != modo_default_atual:
                with st.spinner("Atualizando default global..."):
                    if _set_default_modo(modo_novo):
                        st.toast(f"Default agora é {modo_novo}", icon="✅")
                        st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    # ───────────────────────────────────────────────────────────────────
    # CARREGA DADOS DAS CAMPANHAS
    # ───────────────────────────────────────────────────────────────────
    data = _zapi_get("validacao")
    if _mostrar_erro_e_parar(data, "(carregando pendências)"):
        return

    linhas = data.get("linhas", [])
    if not linhas:
        st.success("🎉 Nada na fila! Todas as validações estão em dia.")
        return

    df = pd.DataFrame(linhas)
    df["data_hora_dt"] = df["data_hora"].apply(_parse_iso)
    df["horas_parado"] = df["data_hora_dt"].apply(
        lambda d: ((datetime.now(TZ_SP) - d).total_seconds() / 3600) if d else 0
    )
    df["bia_puxou_em_dt"] = df.get("bia_puxou_em", pd.Series([None] * len(df))).apply(_parse_iso)
    df["modo"] = df.get("modo", pd.Series([""] * len(df))).fillna("").astype(str).str.upper().str.strip()
    df["validacao_marcada"] = df["validacao_marcada"].fillna("").astype(str).str.upper().str.strip()

    # ───────────────────────────────────────────────────────────────────
    # PROGRESSO BIA (Supabase) — só pra campanhas AUTO que Bia já puxou
    # ───────────────────────────────────────────────────────────────────
    camp_ids_bia = tuple(
        df[(df["modo"] == "AUTO") & df["bia_puxou_em_dt"].notna()]["campanha_id"].dropna().tolist()
    )
    progresso_por_camp = _get_progresso_campanhas_bia(camp_ids_bia)

    # ───────────────────────────────────────────────────────────────────
    # CARDS DE RESUMO
    # ───────────────────────────────────────────────────────────────────
    _marcadas = df["validacao_marcada"].isin(["VALIDADO", "INVALIDADO", "AUTO_VALIDADO_BIA", "AUTO_INVALIDADO_BIA"])
    qtd_processando = int(_marcadas.sum())
    df_ativas = df[~_marcadas]

    # Subdivisão por modo (entre as ativas)
    qtd_sem_modo = int((df_ativas["modo"] == "").sum())
    qtd_manual = int((df_ativas["modo"] == "MANUAL").sum())
    qtd_auto_puxado = int(((df_ativas["modo"] == "AUTO") & df_ativas["bia_puxou_em_dt"].notna()).sum())
    qtd_auto_aguardando = int(((df_ativas["modo"] == "AUTO") & df_ativas["bia_puxou_em_dt"].isna()).sum())

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric(
        "⚠️ Sem decisão", qtd_sem_modo,
        help="Coordenadora ainda não escolheu MANUAL ou AUTO"
    )
    col_m2.metric(
        "👤 Manual", qtd_manual,
        help="Aguardando captadora ligar pros indicados"
    )
    col_m3.metric(
        "🤖 AUTO (Bia rodando)", qtd_auto_puxado,
        help="Bia já puxou o lote, contando respostas"
    )
    col_m4.metric(
        "⏳ Em processamento", qtd_processando,
        help="Já decididas (manual ou AUTO), aguardando trigger 5min disparar voucher/mensagem"
    )

    st.markdown("---")

    # Filtro por unidade
    unid_filtro = st.radio(
        "Filtrar por unidade:",
        ["Todas", "Mogi", "Suzano"],
        horizontal=True,
        key="zapi_aguard_unidade",
    )
    if unid_filtro != "Todas":
        df = df[df["unidade"].str.lower() == unid_filtro.lower()]
        df_ativas = df_ativas[df_ativas["unidade"].str.lower() == unid_filtro.lower()]

    if df.empty:
        st.info(f"Nada pendente em {unid_filtro}.")
        return

    # ───────────────────────────────────────────────────────────────────
    # BANNER DE "PROCESSANDO" (já marcadas, aguardando trigger 5min)
    # ───────────────────────────────────────────────────────────────────
    df_proc = df[df["validacao_marcada"].isin(["VALIDADO", "INVALIDADO", "AUTO_VALIDADO_BIA", "AUTO_INVALIDADO_BIA"])]
    if not df_proc.empty:
        nomes_proc = ", ".join(df_proc["nome"].tolist()[:5])
        extras = f" e mais {len(df_proc) - 5}" if len(df_proc) > 5 else ""
        st.info(
            f"⏳ **{len(df_proc)} campanha(s) processando:** {nomes_proc}{extras}. "
            f"Trigger do Apps Script vai disparar voucher/mensagem em até 5min."
        )

    if df_ativas.empty:
        st.success("🎉 Sem campanhas aguardando ação.")
        return

    st.markdown(f"### {len(df_ativas)} campanha(s) na fila")

    # CSS local pros badges e cards
    st.markdown(
        """
    <style>
    .urg-urgente { background: #fee2e2; color: #991b1b; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .urg-atencao { background: #fef3c7; color: #92400e; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .urg-ok      { background: #dcfce7; color: #166534; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .priv-anonimo      { background: #f3e8ff; color: #6b21a8; padding: 1px 8px; border-radius: 8px; font-size: 11px; }
    .priv-identificado { background: #dbeafe; color: #1e40af; padding: 1px 8px; border-radius: 8px; font-size: 11px; }
    .priv-vazia        { background: #f3f4f6; color: #6b7280; padding: 1px 8px; border-radius: 8px; font-size: 11px; }
    .modo-manual { background: #fef3c7; color: #92400e; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .modo-auto-rodando { background: #dbeafe; color: #1e40af; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .modo-auto-aguarda { background: #e0e7ff; color: #3730a3; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .modo-vazio { background: #fee2e2; color: #991b1b; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .progress-bg { background: #e5e7eb; border-radius: 8px; height: 22px; overflow: hidden; margin-top: 4px; }
    .progress-fill { background: linear-gradient(90deg, #5BC0BE 0%, #3D9991 100%); height: 100%; border-radius: 8px; transition: width 0.6s ease; }
    .card-acao { background: #fafafa; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; margin-top: 8px; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    # Ordena: sem decisão primeiro (mais urgente), depois por tempo parado
    def _ordem_prioridade(row):
        if row["modo"] == "":
            return (0, -row["horas_parado"])  # sem decisão, mais antigos primeiro
        if row["modo"] == "AUTO" and row["bia_puxou_em_dt"] is not None:
            return (1, -row["horas_parado"])  # AUTO rodando
        if row["modo"] == "AUTO":
            return (2, -row["horas_parado"])  # AUTO aguardando puxar
        return (3, -row["horas_parado"])  # MANUAL

    df_ativas = df_ativas.assign(
        _prio=df_ativas.apply(_ordem_prioridade, axis=1)
    ).sort_values("_prio").reset_index(drop=True)

    # ───────────────────────────────────────────────────────────────────
    # RENDERIZA CADA CARD
    # ───────────────────────────────────────────────────────────────────
    for _, row in df_ativas.iterrows():
        _renderizar_card_campanha(row, progresso_por_camp, modo_default_atual)


# ============================================================================
# RENDERIZA UM CARD INDIVIDUAL DE CAMPANHA
# ============================================================================

def _renderizar_card_campanha(row, progresso_por_camp, modo_default_atual):
    tel = row["telefone"]
    nome = row["nome"] or "(sem nome)"
    func = row["funcionaria"] or "—"
    unid = row["unidade"] or "—"
    contatos = int(row["contatos"] or 0)
    priv = str(row.get("privacidade") or "").upper()
    camp_id = row["campanha_id"]
    dt = row["data_hora_dt"]
    tempo = _humanizar_tempo(dt)
    urg = _classe_urgencia(dt)
    modo_atual = row["modo"]
    bia_puxou = row["bia_puxou_em_dt"]

    urg_label = {"urgente": "🔴 URGENTE", "atencao": "🟡 ATENÇÃO", "ok": "🟢 OK"}[urg]
    priv_label = {"ANONIMO": "🤫 anônima", "IDENTIFICADO": "✨ identificada"}.get(priv, "— sem privacidade")
    priv_class = {"ANONIMO": "priv-anonimo", "IDENTIFICADO": "priv-identificado"}.get(priv, "priv-vazia")

    # Badge de modo
    if modo_atual == "":
        modo_html = '<span class="modo-vazio">⚠️ SEM DECISÃO</span>'
    elif modo_atual == "MANUAL":
        modo_html = '<span class="modo-manual">👤 MANUAL</span>'
    elif modo_atual == "AUTO" and bia_puxou is not None:
        modo_html = '<span class="modo-auto-rodando">🤖 AUTO · BIA RODANDO</span>'
    else:
        modo_html = '<span class="modo-auto-aguarda">🤖 AUTO · AGUARDANDO PUXAR</span>'

    with st.container():
        # Header do card
        st.markdown(
            f"""
            <div style="padding: 12px 14px; border: 1px solid #e5e7eb; border-radius: 10px; margin-bottom: 8px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <div>
                  <span style="font-size: 16px; font-weight: 700;">{nome}</span>
                  &nbsp;<span class="{priv_class}">{priv_label}</span>
                  &nbsp;{modo_html}
                </div>
                <span class="urg-{urg}">{urg_label} · {tempo}</span>
              </div>
              <div style="color: #6b7280; font-size: 13px;">
                📱 {_formatar_telefone(tel)} &nbsp;·&nbsp;
                👩‍💼 {func} ({unid}) &nbsp;·&nbsp;
                📨 {contatos} contatos
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ───────────────────────────────────────────────────────────────
        # AÇÕES (variam conforme estado)
        # ───────────────────────────────────────────────────────────────
        estado = _detectar_estado_campanha(modo_atual, bia_puxou)

        if estado == "sem_decisao":
            _render_acao_sem_decisao(camp_id, tel, nome, modo_default_atual)

        elif estado == "manual":
            _render_acao_manual(camp_id, tel, nome, bia_puxou)

        elif estado == "auto_aguardando":
            _render_acao_auto_aguardando(camp_id, tel, nome)

        elif estado == "auto_rodando":
            _render_acao_auto_rodando(camp_id, contatos, bia_puxou, progresso_por_camp)

        # ───────────────────────────────────────────────────────────────
        # VER CONTATOS (toggle pra todos os estados)
        # ───────────────────────────────────────────────────────────────
        ver_contatos = st.toggle(
            "👁️ Ver os 20 contatos enviados",
            key=f"toggle_ver_{camp_id}",
        )
        if ver_contatos:
            _render_lista_contatos(camp_id, nome)

        st.markdown("")  # respiro entre cards


# ============================================================================
# HELPERS DE ESTADO + RENDERIZAÇÃO DE AÇÕES POR ESTADO
# ============================================================================

def _detectar_estado_campanha(modo, bia_puxou_dt):
    """Retorna: 'sem_decisao' | 'manual' | 'auto_aguardando' | 'auto_rodando'"""
    if modo == "":
        return "sem_decisao"
    if modo == "MANUAL":
        return "manual"
    if modo == "AUTO" and bia_puxou_dt is None:
        return "auto_aguardando"
    if modo == "AUTO" and bia_puxou_dt is not None:
        return "auto_rodando"
    return "sem_decisao"  # fallback


def _render_acao_sem_decisao(camp_id, tel, nome, modo_default_atual):
    """Estado: campanha nova, coordenadora precisa escolher MODO."""
    sugestao = "AUTO" if modo_default_atual == "AUTO" else "MANUAL"

    st.markdown(
        f"""
        <div class="card-acao">
        <strong>⚠️ Coordenadora precisa escolher o modo:</strong>
        <span style="color: #6b7280; font-size: 12px;">  (default global: <strong>{sugestao}</strong>)</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_m, col_a, _ = st.columns([1.2, 1.2, 2])
    with col_m:
        if st.button(
            "👤 MANUAL (captadora liga)",
            key=f"set_manual_{camp_id}",
            use_container_width=True,
            help="Captadora liga pros indicados pra validar. Você aperta Validar/Invalidar depois.",
        ):
            _executar_set_modo(tel, "MANUAL", nome)
    with col_a:
        if st.button(
            "🤖 AUTO (Bia trabalha)",
            key=f"set_auto_{camp_id}",
            type="primary",
            use_container_width=True,
            help="Bia v5 dispara templates pros 20 indicados. Auto-valida com 30% de respostas em até 36h.",
        ):
            _executar_set_modo(tel, "AUTO", nome)


def _render_acao_manual(camp_id, tel, nome, bia_puxou_dt):
    """Estado: MANUAL clássico — captadora liga, coordenadora aperta Validar/Invalidar."""

    st.markdown(
        '<div class="card-acao"><strong>👤 Modo MANUAL:</strong> '
        'captadora liga pros indicados. Após contato, aperte abaixo:</div>',
        unsafe_allow_html=True,
    )

    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_a:
        btn_validar = st.button(
            "✅ Validar",
            key=f"btn_val_{camp_id}",
            use_container_width=True,
            help="Marca VALIDADO. Voucher dispara automático em até 5min.",
        )
    with col_b:
        btn_invalidar = st.button(
            "❌ Invalidar",
            key=f"btn_inv_{camp_id}",
            use_container_width=True,
            help="Marca INVALIDADO. Mensagem de invalidação dispara em até 5min.",
        )
    with col_c:
        # Mudar pra AUTO só se Bia ainda não puxou
        if bia_puxou_dt is None:
            if st.button(
                "↩️ Mudar pra AUTO",
                key=f"to_auto_{camp_id}",
                use_container_width=True,
                help="Cancela MANUAL e deixa a Bia trabalhar este lote.",
            ):
                _executar_set_modo(tel, "AUTO", nome)
        else:
            st.button(
                "↩️ Mudar pra AUTO",
                key=f"to_auto_disabled_{camp_id}",
                disabled=True,
                use_container_width=True,
                help="Não dá mais — Bia já trabalhou esse lote.",
            )

    # Confirmação dupla pra Validar/Invalidar
    if btn_validar or btn_invalidar:
        decisao = "VALIDADO" if btn_validar else "INVALIDADO"
        st.session_state[f"confirm_pending_{camp_id}"] = decisao

    if st.session_state.get(f"confirm_pending_{camp_id}"):
        decisao = st.session_state[f"confirm_pending_{camp_id}"]
        cor_aviso = "#dc2626" if decisao == "VALIDADO" else "#f59e0b"
        msg_aviso = (
            f"⚠️ Confirmar **{decisao}** pra **{nome}**? "
            + (
                "Voucher de Revitalização Facial vai disparar."
                if decisao == "VALIDADO"
                else "Mensagem de invalidação vai disparar."
            )
        )
        st.markdown(
            f"<div style='padding: 10px; background: #fff7ed; border-left: 4px solid {cor_aviso}; border-radius: 6px; margin: 8px 0;'>{msg_aviso}</div>",
            unsafe_allow_html=True,
        )
        col_sim, col_nao = st.columns([1, 1])
        with col_sim:
            confirmar = st.button(
                "✔️ Confirmar",
                key=f"confirm_{camp_id}",
                type="primary",
                use_container_width=True,
            )
        with col_nao:
            cancelar = st.button(
                "✖️ Cancelar", key=f"cancel_{camp_id}", use_container_width=True
            )

        if cancelar:
            st.session_state.pop(f"confirm_pending_{camp_id}", None)
            st.rerun()

        if confirmar:
            with st.spinner(f"Marcando {decisao}..."):
                # Modo MANUAL explícito (mesmo sendo default no Apps Script)
                resp = _zapi_action("marcar_validacao", tel=tel, decisao=decisao, modo="MANUAL")
            if resp.get("_erro") or resp.get("erro"):
                st.error(f"❌ Falhou: {resp.get('_erro') or resp.get('erro')}")
            elif resp.get("ja_marcado"):
                st.warning(f"ℹ️ Já estava marcado como {decisao} (alguém adiantou).")
                st.session_state.pop(f"confirm_pending_{camp_id}", None)
                _zapi_get.clear()
            else:
                st.success(
                    f"✅ {decisao} marcado! Trigger vai processar em até 5min e disparar a mensagem pra cliente."
                )
                st.session_state.pop(f"confirm_pending_{camp_id}", None)
                _zapi_get.clear()
                st.balloons()
            st.rerun()


def _render_acao_auto_aguardando(camp_id, tel, nome):
    """Estado: MODO=AUTO mas Bia ainda não puxou o lote."""
    st.markdown(
        '<div class="card-acao">'
        '<strong>🤖 Modo AUTO selecionado.</strong> '
        'Bia v5 vai puxar este lote no próximo ciclo do Cron 6 v2 '
        '(a cada 20min entre 10h e 19h). '
        '<br><br>'
        '<small>Você ainda pode voltar pra MANUAL enquanto Bia não puxar.</small>'
        '</div>',
        unsafe_allow_html=True,
    )

    col_a, _ = st.columns([1.5, 3])
    with col_a:
        if st.button(
            "↩️ Mudar pra MANUAL",
            key=f"to_manual_{camp_id}",
            use_container_width=True,
            help="Cancela AUTO. Captadora vai ter que ligar manualmente.",
        ):
            _executar_set_modo(tel, "MANUAL", nome)


def _render_acao_auto_rodando(camp_id, contatos, bia_puxou_dt, progresso_por_camp):
    """Estado: MODO=AUTO, Bia já puxou o lote. Mostra progresso, sem botões."""
    respostas = progresso_por_camp.get(camp_id, 0)
    meta = _meta_respostas(contatos)
    pct = int(min(100, (respostas / meta * 100) if meta > 0 else 0))

    # Tempo desde Bia puxar
    agora = datetime.now(TZ_SP)
    horas_rodando = (agora - bia_puxou_dt).total_seconds() / 3600
    horas_restantes = max(0, 36 - horas_rodando)
    timeout_iminente = horas_restantes < 6

    cor_timeout = "#ef4444" if timeout_iminente else "#6b7280"

    st.markdown(
        f"""
        <div class="card-acao">
        <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
            <div>
                🤖 <strong>Bia trabalhando há {horas_rodando:.1f}h</strong>
            </div>
            <div style="color: {cor_timeout}; font-weight: 600;">
                ⏰ {horas_restantes:.1f}h até timeout
            </div>
        </div>
        <div style="margin-top: 8px;">
            📊 <strong>Progresso:</strong> {respostas} / {meta} respostas ({pct}%)
            <div class="progress-bg">
                <div class="progress-fill" style="width: {pct}%;"></div>
            </div>
        </div>
        <div style="margin-top: 10px; font-size: 12px; color: #6b7280;">
            ℹ️ Bia auto-valida ao bater {meta} respostas, ou auto-invalida após 36h.
            Coordenadora não precisa fazer nada.
        </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================================
# AÇÕES AUXILIARES
# ============================================================================

def _executar_set_modo(tel, modo, nome):
    """Chama set_modo_campanha no Apps Script + trata resposta + rerun."""
    with st.spinner(f"Definindo modo {modo} pra {nome}..."):
        resp = _zapi_action("set_modo_campanha", tel=tel, modo=modo)
    if resp.get("_erro") or resp.get("erro"):
        st.error(f"❌ Falhou: {resp.get('_erro') or resp.get('erro')}")
    else:
        st.toast(f"Modo {modo} aplicado pra {nome}", icon="✅")
        _zapi_get.clear()
        _get_progresso_campanhas_bia.clear()
        st.rerun()


def _render_lista_contatos(camp_id, nome):
    """Bloco expansível com os 20 contatos da campanha."""
    with st.spinner(f"Carregando contatos da {nome}..."):
        contatos_data = _zapi_get("contatos_cliente", campanha_id=camp_id)
    if _mostrar_erro_e_parar(contatos_data, "(carregando contatos)"):
        return

    contatos_lista = contatos_data.get("linhas", [])
    if not contatos_lista:
        st.info("Nenhum contato encontrado nessa campanha (estranho).")
        return

    df_c = pd.DataFrame(contatos_lista)
    df_c["telefone_formatado"] = df_c["telefone_indicado"].apply(_formatar_telefone)
    df_c = df_c[["nome_indicado", "telefone_formatado"]].rename(
        columns={"nome_indicado": "Nome", "telefone_formatado": "Telefone"}
    )
    st.dataframe(df_c, use_container_width=True, hide_index=True)
    st.caption(f"📋 {len(contatos_lista)} contatos indicados pela cliente")


# ============================================================================
# TELA: 🏆 RANKING FUNCIONÁRIAS
# ============================================================================
# Lê do endpoint funcionarias_real (Apps Script v9.3) que calcula em tempo real
# a partir de CLIENTES + CLIENTES_ARQUIVO + INDICACOES + INDICACOES_ARQUIVO,
# normalizando lowercase (case-insensitive) e filtrando 'teste'.
#
# Critério "trouxe cliente" = coluna DATA BATEU META preenchida.
# A aba FUNCIONARIAS NÃO é usada aqui (cálculo dela puxa errado por homônimas
# e ignora arquivados).
# ============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _zapi_get_ranking(data_inicio: str = "", data_fim: str = ""):
    """
    Chama o endpoint funcionarias_real do Apps Script com filtro opcional de período.
    Cache 5min por combinação de datas (cada filtro tem seu próprio cache).

    Args:
        data_inicio: ISO date YYYY-MM-DD ou string vazia pra sem filtro
        data_fim: idem
    """
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente: APPS_SCRIPT_URL_ZAPI / APPS_SCRIPT_TOKEN_ZAPI"}

    params = {"endpoint": "funcionarias_real", "token": token}
    if data_inicio:
        params["data_inicio"] = data_inicio
    if data_fim:
        params["data_fim"] = data_fim

    try:
        resp = requests.get(
            url,
            params=params,
            timeout=30,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code} ao calcular ranking"}
        data = resp.json()
        if isinstance(data, dict) and data.get("erro"):
            return {"_erro": f"Z-API: {data['erro']}"}
        return data
    except requests.exceptions.Timeout:
        return {"_erro": "Apps Script demorou demais (>30s) — provavelmente carga alta. Tente novamente."}
    except requests.exceptions.RequestException as e:
        return {"_erro": f"Erro de rede: {e}"}
    except ValueError:
        return {"_erro": "Resposta do Apps Script não é JSON válido."}


# ============================================================================
# PATCH — função tela_zapi_ranking (corrige mismatch com endpoint funcionarias_real)
# ----------------------------------------------------------------------------
# COMO APLICAR (GitHub web UI):
#   1) Abre https://github.com/carlosandreucci88-afk/dashboard-bia-maislaser
#   2) Clica em `aba_zapi.py`
#   3) Clica no ícone de lápis (Edit this file)
#   4) Ctrl+F → busca: `def tela_zapi_ranking():`
#   5) Seleciona desde `def tela_zapi_ranking():` até o final dessa função
#      (a próxima função após ela é `tela_zapi_indicacoes` ou outra parecida)
#   6) Substitui pelo código abaixo
#   7) Commit pra branch `main` (o Railway redeploya em ~1min)
# ============================================================================

def tela_zapi_ranking():
    st.markdown("## 🏆 Ranking de funcionárias")
    st.caption(
        "Calculado em tempo real a partir de CLIENTES + arquivo + INDICACOES + arquivo. "
        "Conta como **cliente** quem bateu meta (enviou os 20 contatos válidos). "
        "Conta como **indicação** cada contato indicado por essas clientes."
    )

    # ─── Seletor de período ──────────────────────────────────────────────
    from datetime import date, timedelta

    hoje = date.today()
    primeiro_dia_mes = hoje.replace(day=1)
    ultimo_dia_mes_passado = primeiro_dia_mes - timedelta(days=1)
    primeiro_dia_mes_passado = ultimo_dia_mes_passado.replace(day=1)

    PRESETS = {
        "📅 Todo o período": (None, None),
        "🗓️ Hoje": (hoje, hoje),
        "📆 Últimos 7 dias": (hoje - timedelta(days=6), hoje),
        "🗓️ Últimos 30 dias": (hoje - timedelta(days=29), hoje),
        "📅 Este mês": (primeiro_dia_mes, hoje),
        "📆 Mês passado": (primeiro_dia_mes_passado, ultimo_dia_mes_passado),
        "🎯 Personalizado": (None, None),
    }

    col_preset, col_atualizar = st.columns([4, 1])
    with col_preset:
        preset_escolhido = st.selectbox(
            "Período:",
            list(PRESETS.keys()),
            index=0,
            key="rank_periodo_preset",
        )
    with col_atualizar:
        st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        if st.button("🔄 Atualizar", key="rank_refresh", use_container_width=True):
            _zapi_get_ranking.clear()
            st.rerun()

    data_inicio_str = ""
    data_fim_str = ""

    if preset_escolhido == "🎯 Personalizado":
        col_di, col_df = st.columns(2)
        with col_di:
            di = st.date_input(
                "Data início:",
                value=hoje - timedelta(days=30),
                max_value=hoje,
                key="rank_data_inicio",
                format="DD/MM/YYYY",
            )
        with col_df:
            df_data = st.date_input(
                "Data fim:",
                value=hoje,
                max_value=hoje,
                key="rank_data_fim",
                format="DD/MM/YYYY",
            )
        if di > df_data:
            st.error("⚠️ Data início não pode ser maior que data fim.")
            return
        data_inicio_str = di.isoformat()
        data_fim_str = df_data.isoformat()
    else:
        di, df_data = PRESETS[preset_escolhido]
        if di and df_data:
            data_inicio_str = di.isoformat()
            data_fim_str = df_data.isoformat()

    # Label legível do período aplicado
    if data_inicio_str and data_fim_str:
        di_fmt = "/".join(reversed(data_inicio_str.split("-")))
        df_fmt = "/".join(reversed(data_fim_str.split("-")))
        if data_inicio_str == data_fim_str:
            st.caption(f"📍 Mostrando dados de **{di_fmt}**")
        else:
            st.caption(f"📍 Mostrando dados de **{di_fmt}** até **{df_fmt}**")
    else:
        st.caption("📍 Mostrando **todo o histórico** (CLIENTES + arquivo + INDICACOES + arquivo)")

    with st.spinner("Calculando ranking..."):
        data = _zapi_get_ranking(data_inicio_str, data_fim_str)

    if _mostrar_erro_e_parar(data, "(carregando ranking)"):
        return

    # ─── FIX v9.9: endpoint retorna `linhas`, não `ranking`. ──────────────
    # Antes: data.get("ranking", []) sempre retornava []
    # Agora: lê data["linhas"] que é o formato real do endpoint
    # ─────────────────────────────────────────────────────────────────────
    linhas = data.get("linhas", [])
    if not linhas:
        st.warning("Nenhum dado no ranking ainda.")
        return

    df = pd.DataFrame(linhas)

    # ─── FIX v9.9: adaptar nomes de campos ──────────────────────────────
    # Endpoint retorna: disparos, indicacoes_validas, vouchers_validados, taxa_conversao
    # Dashboard usa internamente: clientes_com_indicacoes, indic_por_cliente
    # Renomear pra manter compat com o resto do código:
    if "disparos" in df.columns:
        df = df.rename(columns={"disparos": "clientes_com_indicacoes"})
    # Calcular indic_por_cliente (não vem do endpoint)
    df["indic_por_cliente"] = df.apply(
        lambda r: round(r["indicacoes_validas"] / r["clientes_com_indicacoes"], 1)
                  if r["clientes_com_indicacoes"] > 0 else 0,
        axis=1,
    )

    # ─── Filtro por unidade ───
    unid_filtro = st.radio(
        "Filtrar por unidade:",
        ["Todas", "Mogi", "Suzano"],
        horizontal=True,
        key="rank_unid_filtro",
    )
    df_filtrado = df.copy()
    if unid_filtro != "Todas":
        df_filtrado = df_filtrado[df_filtrado["unidade"].str.lower() == unid_filtro.lower()]

    # ─── Cards de resumo — calculados a partir do DF filtrado ───
    n_func = len(df_filtrado)
    n_cli = int(df_filtrado["clientes_com_indicacoes"].sum())
    n_ind = int(df_filtrado["indicacoes_validas"].sum())
    n_vouch = int(df_filtrado["vouchers_validados"].sum()) if "vouchers_validados" in df_filtrado.columns else 0

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("👥 Funcionárias", n_func, help="Funcionárias com pelo menos 1 cliente ou indicação no período")
    col_b.metric("🎯 Bateram meta", n_cli,
        help="Clientes que enviaram 20 contatos válidos (= 'disparos' no endpoint)")
    col_c.metric("📨 Indicações", f"{n_ind:,}".replace(",", "."),
        help="Total de contatos indicados pelas clientes que bateram meta")
    col_d.metric("🎁 Vouchers", n_vouch,
        help="Clientes que tiveram voucher liberado (status FINALIZADO)")

    st.markdown("---")

    # Substitui df pelo filtrado pra resto da tela usar
    df = df_filtrado

    if df.empty:
        st.info(f"Nenhuma funcionária em {unid_filtro}.")
        return

    df = df.sort_values("indicacoes_validas", ascending=False).reset_index(drop=True)

    # ─── Top 5 com cards medalha ───
    st.markdown("### 🥇 Top 5")
    top5 = df.head(5)
    medalhas = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    cols = st.columns(min(5, len(top5)))
    for i, (_, r) in enumerate(top5.iterrows()):
        with cols[i]:
            st.markdown(
                f"""
                <div style="padding: 14px; border: 1px solid #e5e7eb; border-radius: 10px;
                            text-align: center; background: #fafafa; min-height: 150px;">
                  <div style="font-size: 28px;">{medalhas[i]}</div>
                  <div style="font-weight: 700; font-size: 14px; margin-top: 4px;">
                    {r['funcionaria']}
                  </div>
                  <div style="color: #6b7280; font-size: 12px;">{r['unidade']}</div>
                  <div style="margin-top: 8px; font-size: 22px; font-weight: 700; color: #059669;">
                    {int(r['indicacoes_validas']):,}
                  </div>
                  <div style="color: #6b7280; font-size: 11px;">indicações</div>
                  <div style="margin-top: 4px; font-size: 12px; color: #374151;">
                    {int(r['clientes_com_indicacoes'])} cliente(s) c/ meta
                  </div>
                </div>
                """.replace(",", "."),
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ─── Tabela completa ───
    st.markdown("### 📋 Ranking completo")
    df_tabela = df.copy()
    df_tabela.insert(0, "#", range(1, len(df_tabela) + 1))

    # Inclui taxa_conversao se vier do endpoint
    cols_tabela = ["#", "funcionaria", "unidade", "clientes_com_indicacoes",
                   "indicacoes_validas", "indic_por_cliente"]
    if "vouchers_validados" in df_tabela.columns:
        cols_tabela.append("vouchers_validados")
    if "taxa_conversao" in df_tabela.columns:
        cols_tabela.append("taxa_conversao")

    df_tabela = df_tabela[cols_tabela]
    df_tabela = df_tabela.rename(columns={
        "funcionaria": "Funcionária",
        "unidade": "Unidade",
        "clientes_com_indicacoes": "Bateram meta",
        "indicacoes_validas": "Indicações",
        "indic_por_cliente": "Indic / cliente",
        "vouchers_validados": "Vouchers",
        "taxa_conversao": "Conversão %",
    })

    column_config = {
        "#": st.column_config.NumberColumn(width="small"),
        "Indicações": st.column_config.NumberColumn(format="%d"),
        "Indic / cliente": st.column_config.NumberColumn(format="%.1f"),
    }
    if "Vouchers" in df_tabela.columns:
        column_config["Vouchers"] = st.column_config.NumberColumn(format="%d")
    if "Conversão %" in df_tabela.columns:
        column_config["Conversão %"] = st.column_config.TextColumn()

    st.dataframe(
        df_tabela,
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
    )

    st.markdown("---")

    # ─── Gráfico de barras horizontal ───
    st.markdown("### 📊 Indicações por funcionária")
    try:
        import plotly.express as px
        df_plot = df.copy()
        df_plot["label"] = df_plot["funcionaria"] + " (" + df_plot["unidade"] + ")"
        df_plot = df_plot.sort_values("indicacoes_validas", ascending=True)
        fig = px.bar(
            df_plot,
            x="indicacoes_validas",
            y="label",
            orientation="h",
            color="unidade",
            color_discrete_map={"mogi": "#6366f1", "suzano": "#f59e0b",
                                "Mogi": "#6366f1", "Suzano": "#f59e0b"},
            text="indicacoes_validas",
            labels={"indicacoes_validas": "Indicações", "label": "", "unidade": "Unidade"},
        )
        fig.update_traces(textposition="outside", textfont_size=11)
        fig.update_layout(
            height=max(300, 30 * len(df_plot) + 100),
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(showgrid=True, gridcolor="#e5e7eb"),
            yaxis=dict(showgrid=False),
            plot_bgcolor="white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.info(f"Gráfico indisponível: {e}")

    # Footer com info do dado
    st.caption(
        f"📅 Calculado em {data.get('gerado_em', '—')} · "
        f"Cache 5min (clica em 🔄 Atualizar pra forçar refresh)"
    )

# ============================================================================
# TELA: 📨 INDICAÇÕES (v9.5)
# ============================================================================

@st.cache_data(ttl=120, show_spinner=False)
def _zapi_get_indicacoes(data_inicio: str = "", data_fim: str = "",
                          incluir_arquivo: bool = False,
                          busca: str = "", status: str = "",
                          unidade: str = "", funcionaria: str = "",
                          limit: int = 5000):
    """Chama o endpoint indicacoes com filtros. Cache 2min por combinação."""
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente: APPS_SCRIPT_URL_ZAPI / APPS_SCRIPT_TOKEN_ZAPI"}

    params = {"endpoint": "indicacoes", "token": token, "limit": str(limit)}
    if data_inicio:     params["data_inicio"] = data_inicio
    if data_fim:        params["data_fim"] = data_fim
    if incluir_arquivo: params["incluir_arquivo"] = "true"
    if busca:           params["busca"] = busca
    if status:          params["status"] = status
    if unidade:         params["unidade"] = unidade
    if funcionaria:     params["funcionaria"] = funcionaria

    try:
        resp = requests.get(url, params=params, timeout=45, allow_redirects=True)
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code} ao buscar indicações"}
        data = resp.json()
        if isinstance(data, dict) and data.get("erro"):
            return {"_erro": f"Z-API: {data['erro']}"}
        return data
    except requests.exceptions.Timeout:
        return {"_erro": "Apps Script demorou demais (>45s). Reduza o período ou desmarque o arquivo."}
    except requests.exceptions.RequestException as e:
        return {"_erro": f"Erro de rede: {e}"}
    except ValueError:
        return {"_erro": "Resposta do Apps Script não é JSON válido."}


def _xlsx_indicacoes(df_export, sufixo_arquivo):
    """Gera XLSX em memória pra download das indicações filtradas."""
    if df_export is None or df_export.empty:
        st.download_button("📥 Exportar XLSX (sem dados)", data=b"",
            file_name="vazio.xlsx", disabled=True,
            key=f"exp_ind_void_{sufixo_arquivo}")
        return

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        d = df_export.copy()
        for col in d.columns:
            if pd.api.types.is_datetime64_any_dtype(d[col]):
                try: d[col] = d[col].dt.tz_localize(None)
                except (TypeError, AttributeError): pass
        d.to_excel(writer, index=False, sheet_name="indicacoes")

    ts = datetime.now(TZ_SP).strftime("%Y%m%d-%H%M")
    fname = f"zapi_indicacoes_{sufixo_arquivo}_{ts}.xlsx"
    st.download_button(
        label=f"📥 Exportar XLSX ({len(df_export)} linhas)",
        data=buf.getvalue(),
        file_name=fname,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"exp_ind_{sufixo_arquivo}",
        help=f"Baixa os {len(df_export)} registros filtrados",
    )


def tela_zapi_indicacoes():
    st.markdown("## 📨 Indicações")
    st.caption(
        "Cada linha é um contato indicado por um cliente. "
        "Inclua o arquivo pra ver histórico completo (3.518 indicações de maio)."
    )

    # ─── Filtros linha 1: período + arquivo + atualizar ────────────────
    hoje = date.today()
    primeiro_mes = hoje.replace(day=1)
    ult_dia_mp = primeiro_mes - timedelta(days=1)
    primeiro_mp = ult_dia_mp.replace(day=1)

    PRESETS = {
        "📅 Todo o período": (None, None),
        "🗓️ Hoje": (hoje, hoje),
        "📆 Últimos 7 dias": (hoje - timedelta(days=6), hoje),
        "🗓️ Últimos 30 dias": (hoje - timedelta(days=29), hoje),
        "📅 Este mês": (primeiro_mes, hoje),
        "📆 Mês passado": (primeiro_mp, ult_dia_mp),
        "🎯 Personalizado": (None, None),
    }

    col_per, col_arq, col_btn = st.columns([3, 2, 1])
    with col_per:
        preset = st.selectbox("Período:", list(PRESETS.keys()), index=0, key="ind_periodo")
    with col_arq:
        st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        incluir_arq = st.toggle("📦 Incluir arquivo (maio)", value=False, key="ind_inc_arq",
            help="Ativa pra incluir os 3.518 contatos das campanhas arquivadas")
    with col_btn:
        st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        if st.button("🔄 Atualizar", key="ind_refresh", use_container_width=True):
            _zapi_get_indicacoes.clear()
            st.rerun()

    data_inicio_str = ""
    data_fim_str = ""
    if preset == "🎯 Personalizado":
        cdi, cdf = st.columns(2)
        with cdi:
            di = st.date_input("Data início:", value=hoje - timedelta(days=30),
                max_value=hoje, key="ind_di", format="DD/MM/YYYY")
        with cdf:
            df_data = st.date_input("Data fim:", value=hoje,
                max_value=hoje, key="ind_df", format="DD/MM/YYYY")
        if di > df_data:
            st.error("⚠️ Data início não pode ser maior que data fim.")
            return
        data_inicio_str = di.isoformat()
        data_fim_str = df_data.isoformat()
    else:
        di, df_data = PRESETS[preset]
        if di and df_data:
            data_inicio_str = di.isoformat()
            data_fim_str = df_data.isoformat()

    # ─── Filtros linha 2: busca + unidade + funcionária ────────────────
    col_b, col_u, col_f = st.columns([3, 2, 2])
    with col_b:
        busca = st.text_input("🔍 Buscar:", placeholder="Nome ou telefone (cliente ou indicado)",
            key="ind_busca")
    with col_u:
        unid_filtro = st.radio("Unidade:", ["Todas", "Mogi", "Suzano"],
            horizontal=True, key="ind_unid")
    with col_f:
        func_filtro = st.text_input("Funcionária:", placeholder="Ex: rafaela",
            key="ind_func")

    unidade_str = "" if unid_filtro == "Todas" else unid_filtro.lower()

    # ─── Chamada ao endpoint ─────────────────────────────────────────────
    with st.spinner("Carregando indicações..."):
        data = _zapi_get_indicacoes(
            data_inicio=data_inicio_str,
            data_fim=data_fim_str,
            incluir_arquivo=incluir_arq,
            busca=busca.strip(),
            unidade=unidade_str,
            funcionaria=func_filtro.strip(),
            limit=5000,
        )

    if _mostrar_erro_e_parar(data, "(carregando indicações)"):
        return

    linhas = data.get("linhas", [])
    total_filtrado = data.get("total_filtrado", 0)
    total_planilha = data.get("total_planilha", 0)
    total_arquivo = data.get("total_arquivo", 0)
    limit_aplicado = data.get("limit_aplicado", 5000)

    # ─── Cards ───────────────────────────────────────────────────────────
    base_total = total_planilha + (total_arquivo if incluir_arq else 0)
    col_a, col_b_card, col_c, col_d = st.columns(4)
    col_a.metric("📨 Filtradas", f"{total_filtrado:,}".replace(",", "."))
    col_b_card.metric("📊 Base total",
        f"{base_total:,}".replace(",", "."),
        help=f"INDICACOES atual: {total_planilha}\n" +
             (f"INDICACOES_ARQUIVO: {total_arquivo}" if incluir_arq else "(arquivo não incluído)"))
    col_c.metric("👁️ Mostrando", f"{len(linhas):,}".replace(",", "."),
        help=f"Limite por chamada: {limit_aplicado}.")
    col_d.metric("📦 Arquivo", "Incluído" if incluir_arq else "Não incluído")

    if total_filtrado > len(linhas):
        st.warning(f"⚠️ {total_filtrado:,} indicações no filtro, mas só {len(linhas):,} exibidas (limite {limit_aplicado}). Refine ou use XLSX.".replace(",", "."))

    if not linhas:
        st.info("Nenhuma indicação encontrada com os filtros atuais.")
        return

    # ─── Tabela ──────────────────────────────────────────────────────────
    df = pd.DataFrame(linhas)

    if "data" in df.columns:
        df["data_dt"] = pd.to_datetime(df["data"], errors="coerce", utc=True).dt.tz_convert(TZ_SP)
        df["📅 Data"] = df["data_dt"].dt.strftime("%d/%m/%Y %H:%M")

    col_map = {
        "📅 Data": "📅 Data",
        "nome_cliente": "👤 Cliente",
        "telefone_cliente": "📱 Tel cliente",
        "funcionaria": "👩 Funcionária",
        "unidade": "📍 Unidade",
        "nome_indicado": "🎯 Indicado",
        "telefone_indicado": "📱 Tel indicado",
        "status": "✅ Status",
        "motivo": "📝 Motivo",
    }
    cols_display = [c for c in col_map.keys() if c in df.columns or c == "📅 Data"]
    df_display = df[cols_display].rename(columns=col_map)

    if "📍 Unidade" in df_display.columns:
        df_display["📍 Unidade"] = df_display["📍 Unidade"].astype(str).str.title()
    if "👩 Funcionária" in df_display.columns:
        df_display["👩 Funcionária"] = df_display["👩 Funcionária"].astype(str).str.title()

    st.markdown("---")

    col_exp, _ = st.columns([2, 5])
    with col_exp:
        sufixo = preset.split(" ", 1)[-1].lower().replace(" ", "-")
        sufixo = sufixo.replace("ç", "c").replace("ã", "a").replace("é", "e")[:20]
        if unid_filtro != "Todas":
            sufixo += f"_{unid_filtro.lower()}"
        _xlsx_indicacoes(df_display, sufixo)

    st.dataframe(df_display, use_container_width=True, hide_index=True, height=520)


# ============================================================================
# TELA: 📊 MÉTRICAS Z-API (v9.6)
# ============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _zapi_get_metricas(data_inicio: str = "", data_fim: str = ""):
    """Chama o endpoint metricas_funil com filtro opcional de período.
    Cache 5min por combinação de datas."""
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente: APPS_SCRIPT_URL_ZAPI / APPS_SCRIPT_TOKEN_ZAPI"}

    params = {"endpoint": "metricas_funil", "token": token}
    if data_inicio: params["data_inicio"] = data_inicio
    if data_fim:    params["data_fim"] = data_fim

    try:
        resp = requests.get(url, params=params, timeout=45, allow_redirects=True)
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code} ao calcular métricas"}
        data = resp.json()
        if isinstance(data, dict) and data.get("erro"):
            return {"_erro": f"Z-API: {data['erro']}"}
        return data
    except requests.exceptions.Timeout:
        return {"_erro": "Apps Script demorou demais (>45s)"}
    except requests.exceptions.RequestException as e:
        return {"_erro": f"Erro de rede: {e}"}
    except ValueError:
        return {"_erro": "Resposta do Apps Script não é JSON válido."}


def tela_zapi_metricas():
    st.markdown("## 📊 Métricas Z-API — Funil & Conversão")
    st.caption(
        "Visão completa do programa Indique e Ganhe: onde os clientes "
        "convertem, onde travam, e quanto tempo leva."
    )

    # ─── v9.7: Filtro de período personalizado ───────────────────────────
    # Filtra por Data Cadastro (quando cliente entrou no programa).
    # ───────────────────────────────────────────────────────────────────
    from datetime import date, timedelta
    hoje = date.today()

    col_tog, col_di, col_df, col_btn = st.columns([2, 2, 2, 1])
    with col_tog:
        usar_filtro = st.toggle(
            "🎯 Filtrar por período",
            value=False, key="met_usar_filtro",
            help="Liga pra recalcular tudo num período específico (por Data Cadastro do cliente)"
        )

    data_inicio_str = ""
    data_fim_str = ""

    if usar_filtro:
        with col_di:
            di = st.date_input("Data início:", value=hoje - timedelta(days=30),
                max_value=hoje, key="met_di", format="DD/MM/YYYY")
        with col_df:
            df_data = st.date_input("Data fim:", value=hoje,
                max_value=hoje, key="met_df", format="DD/MM/YYYY")
        if di > df_data:
            st.error("⚠️ Data início não pode ser maior que data fim.")
            return
        data_inicio_str = di.isoformat()
        data_fim_str = df_data.isoformat()
    else:
        with col_di:
            st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
            st.caption("Mostrando: **todo o período**")

    with col_btn:
        st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        if st.button("🔄 Atualizar", key="met_refresh", use_container_width=True):
            _zapi_get_metricas.clear()
            st.rerun()

    # Label do período aplicado (visual)
    if usar_filtro:
        di_fmt = "/".join(reversed(data_inicio_str.split("-")))
        df_fmt = "/".join(reversed(data_fim_str.split("-")))
        st.caption(f"📍 Período: **{di_fmt}** até **{df_fmt}**")

    with st.spinner("Calculando métricas do funil..."):
        data = _zapi_get_metricas(data_inicio_str, data_fim_str)

    if _mostrar_erro_e_parar(data, "(carregando métricas)"):
        return

    total = data.get("total_convidados", 0)
    funil = data.get("funil", {})
    drop = data.get("drop_off", {})
    priv = data.get("privacidade", {})
    por_unid = data.get("por_unidade", {})
    tempos = data.get("tempos", {}).get("cadastro_ate_meta", {})
    top5 = data.get("top5_conversao", [])
    fontes = data.get("fontes", {})

    n_voucher = funil.get("n4_recebeu_voucher", {}).get("total", 0)
    pct_voucher = funil.get("n4_recebeu_voucher", {}).get("pct", 0)

    # ─── 4 CARDS MACRO ───
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("👥 Convidados", f"{total:,}".replace(",", "."))
    col_b.metric("🎁 Voucher liberado", f"{n_voucher:,}".replace(",", "."),
        delta=f"{pct_voucher}% conversão", delta_color="normal")
    col_c.metric("⏱️ Mediana cadastro → meta", f"{tempos.get('mediana_h', 0):.1f}h",
        help=f"Amostra: {tempos.get('amostra', 0)} clientes com timestamps válidos")
    col_d.metric("💤 Desistiram", f"{drop.get('desistiu_sem_resp', 0):,}".replace(",", "."),
        delta=f"-{round(drop.get('desistiu_sem_resp', 0)/total*100, 1) if total else 0}%",
        delta_color="inverse",
        help="Pararam de responder após cobrança (_COBRADOSEMRESPOSTA)")

    st.markdown("---")

    # ─── FUNIL VISUAL (barras horizontais) ───
    st.markdown("### 🔻 Funil de conversão")

    niveis = [
        ("👥 Convidados",        funil.get("n1_convidados", {})),
        ("✅ Escolheu privacidade", funil.get("n2_escolheu_priv", {})),
        ("🎯 Bateu meta (20)",   funil.get("n3_bateu_meta", {})),
        ("🎁 Recebeu voucher",   funil.get("n4_recebeu_voucher", {})),
    ]

    for label, dados_nivel in niveis:
        n = dados_nivel.get("total", 0)
        pct = dados_nivel.get("pct", 0)
        # Barra com largura proporcional
        st.markdown(
            f"""<div style="margin: 8px 0;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                    <strong>{label}</strong>
                    <span style="color: #5BC0BE; font-weight: 700;">{n:,} ({pct}%)</span>
                </div>
                <div style="background: #e5e7eb; border-radius: 8px; height: 28px; overflow: hidden;">
                    <div style="background: linear-gradient(90deg, #5BC0BE 0%, #4AA8A6 100%);
                                width: {pct}%; height: 100%; border-radius: 8px;
                                transition: width 0.6s ease;"></div>
                </div>
            </div>""".replace(",", "."),
            unsafe_allow_html=True
        )

    st.markdown("---")

    # ─── DROP-OFF — onde perde clientes ───
    st.markdown("### 📉 Onde perde clientes")

    drops = [
        ("💤 Desistiu sem responder", drop.get("desistiu_sem_resp", 0), "🚨"),
        ("⏸️ Travado em validação",   drop.get("travados_val", 0), ""),
        ("🔒 Travado em privacidade", drop.get("travados_priv", 0), ""),
        ("📞 Travado em contatos",    drop.get("travados_cont", 0), ""),
        ("🚫 Encerrado (invalid. 2x)", drop.get("encerrados", 0), ""),
    ]
    drops.sort(key=lambda x: x[1], reverse=True)

    cols = st.columns(len(drops))
    for col, (label, qtd, badge) in zip(cols, drops):
        pct_d = round(qtd/total*100, 1) if total else 0
        col.metric(label, qtd, delta=f"{pct_d}%" if qtd else "0%",
            delta_color="inverse" if qtd > 5 else "off")
        if badge:
            col.caption(f"{badge} maior gargalo")

    st.markdown("---")

    # ─── COMPARAÇÃO MOGI vs SUZANO ───
    st.markdown("### 🏬 Comparação entre unidades")

    col_m, col_s = st.columns(2)
    for col, key, nome in [(col_m, "mogi", "🏙️ Mogi"), (col_s, "suzano", "🌆 Suzano")]:
        u = por_unid.get(key, {})
        utot = u.get("total", 0)
        ufin = u.get("finalizados", 0)
        upct = round(ufin/utot*100, 1) if utot else 0
        col.markdown(f"#### {nome}")
        col.metric("Total convidados", utot)
        col.metric("Voucher liberado", ufin, delta=f"{upct}% conversão")
        col.metric("Desistiram", u.get("desistiu", 0))
        col.metric("Encerrados", u.get("encerrados", 0))

    st.markdown("---")

    # ─── PRIVACIDADE + TEMPOS ───
    col_p, col_t = st.columns(2)

    with col_p:
        st.markdown("### 🔐 Privacidade")
        ident = priv.get("identificado", 0)
        anon = priv.get("anonimo", 0)
        vazio = priv.get("vazio", 0)
        pct_anon = priv.get("pct_anonimo_dentre_decididos", 0)

        st.markdown(f"""
        - **Identificadas (1):** {ident} cliente{'s' if ident != 1 else ''}
        - **Anônimas (2):** {anon} cliente{'s' if anon != 1 else ''}
        - **Sem registro:** {vazio} cliente{'s' if vazio != 1 else ''} *(antigos pré-v9)*

        Entre quem escolheu: **{pct_anon}% optaram pelo modo anônimo.**
        """)

        if pct_anon > 50:
            st.info(f"💡 Maioria prefere anonimato — talvez seja sinal de que clientes querem indicar mas não querem que amigos saibam.")

    with col_t:
        st.markdown("### ⏱️ Tempos médios")
        amostra = tempos.get('amostra', 0)
        if amostra > 0:
            med = tempos.get('mediana_h', 0)
            mean_ = tempos.get('media_h', 0)
            mn = tempos.get('min_h', 0)
            mx = tempos.get('max_h', 0)
            st.markdown(f"""
            **Cadastro → bater meta** *(amostra: {amostra} clientes)*

            - **Mediana:** {med:.1f}h ⚡
            - **Média:** {mean_:.1f}h
            - **Mais rápido:** {mn:.2f}h ({int(mn*60)}min)
            - **Mais demorado:** {mx:.1f}h
            """)
            if med < 1:
                st.success(f"🚀 Engajamento muito rápido — metade dos clientes completam em menos de 1h.")
        else:
            st.info("Sem dados suficientes (precisa coluna DATA BATEU META preenchida).")

    st.markdown("---")

    # ─── TOP 5 CONVERSÃO ───
    st.markdown("### 🏆 Top 5 funcionárias por taxa de conversão")
    st.caption("Funcionárias com **≥3 convidados**, ordenadas por % de voucher liberado.")

    if top5:
        df_top = pd.DataFrame(top5)
        df_top["📊 Conversão"] = df_top["taxa_conversao"].apply(lambda x: f"{x:.1f}%")
        df_top = df_top[["funcionaria", "unidade", "convidados", "finalizados", "📊 Conversão"]]
        df_top.columns = ["👩 Funcionária", "📍 Unidade", "👥 Convidados", "🎁 Vouchers", "📊 Conversão"]
        df_top["📍 Unidade"] = df_top["📍 Unidade"].astype(str).str.title()
        df_top.index = df_top.index + 1
        df_top.index.name = "🏅"
        st.dataframe(df_top, use_container_width=True)
    else:
        st.info("Ninguém atingiu amostra mínima (3 convidados) ainda.")

    # ─── Fonte ───
    st.caption(
        f"📚 Fonte: {fontes.get('clientes_ativos', 0)} clientes ativos + "
        f"{fontes.get('clientes_arquivo', 0)} arquivados. "
        f"Cache 5min (clica em 🔄 Atualizar pra forçar refresh)."
    )


# ============================================================================
# TELA: 👥 CLIENTES NO PROGRAMA (v9.8)
# ============================================================================

# Classificação visual: status → categoria amigável com emoji
_CATEGORIAS_CLIENTES = [
    ("🔵 Aguardando validação", lambda s: s == 'AGUARDANDO_VALIDACAO'),
    ("🟠 Invalidado (vai encerrar)", lambda s: s == 'INVALIDADO_COBRADO'),
    ("🟠 Invalidado (1ª tentativa)", lambda s: s in ('INVALIDADO', 'INVALIDADO_AVISADO')),
    ("🟡 Privacidade (cobrando)", lambda s: 'PRIVACIDADE' in s and 'COBRADO' in s),
    ("⚪ Privacidade (esperando)", lambda s: s == 'AGUARDANDO_PRIVACIDADE'),
    ("🟡 Contatos (cobrando)", lambda s: 'CONTATOS' in s and 'COBRADO' in s),
    ("⚪ Contatos (esperando)", lambda s: s == 'AGUARDANDO_CONTATOS'),
    ("✅ Finalizado", lambda s: s == 'FINALIZADO'),
    ("🚫 Encerrado", lambda s: s == 'ENCERRADO'),
    ("💤 Desistiu (sem resposta)", lambda s: s == '_COBRADOSEMRESPOSTA'),
]


def _categoria_cliente(status):
    s = str(status).upper() if status else ''
    for cat, predicado in _CATEGORIAS_CLIENTES:
        if predicado(s):
            return cat
    return f"❓ {s}"


def _eh_ativo(status):
    """Cliente em estado ativo (não terminal). Os terminais são FIN/ENC/DES."""
    s = str(status).upper() if status else ''
    return s not in ('FINALIZADO', 'ENCERRADO', '_COBRADOSEMRESPOSTA')


def tela_zapi_clientes_programa():
    st.markdown("## 👥 Clientes no programa")
    st.caption(
        "Todos os clientes em CLIENTES (atual). "
        "Ordenado por tempo no status atual — mais urgente no topo."
    )

    col_btn, _ = st.columns([1, 5])
    with col_btn:
        if st.button("🔄 Atualizar", key="cliprog_refresh", use_container_width=True):
            _zapi_get.clear()
            st.rerun()

    with st.spinner("Carregando clientes..."):
        data = _zapi_get("clientes")

    if _mostrar_erro_e_parar(data, "(carregando clientes)"):
        return

    linhas = data.get("linhas", [])
    if not linhas:
        st.info("Nenhum cliente em CLIENTES no momento.")
        return

    # Monta DataFrame
    df = pd.DataFrame(linhas)

    # Filtra 'teste' (consistente com outras telas)
    if 'Funcionaria' in df.columns:
        df = df[df['Funcionaria'].astype(str).str.lower().str.strip() != 'teste']

    if df.empty:
        st.info("Nenhum cliente real (só 'teste').")
        return

    # Enriquece com categoria + tempo no status
    status_col = 'STATUS DE AONDE PAROU' if 'STATUS DE AONDE PAROU' in df.columns else 'status_rec'
    df['_categoria'] = df[status_col].apply(_categoria_cliente)
    df['_ativo'] = df[status_col].apply(_eh_ativo)

    # Tempo no status
    if 'DATA E HORA' in df.columns:
        df['_data_hora'] = pd.to_datetime(df['DATA E HORA'], errors='coerce', utc=True).dt.tz_convert(TZ_SP)
        agora = datetime.now(TZ_SP)
        df['_horas'] = (agora - df['_data_hora']).dt.total_seconds() / 3600

    # ─── Cards de resumo ───
    n_total = len(df)
    n_ativos = df['_ativo'].sum()
    n_voucher = (df.get('Voucher Liberado', '').astype(str).str.upper() == 'SIM').sum() if 'Voucher Liberado' in df.columns else 0
    n_desistiu = (df[status_col] == '_COBRADOSEMRESPOSTA').sum()

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("👥 Total em CLIENTES", n_total)
    col_b.metric("🔵 Em ação ativa", int(n_ativos),
        help="Não-terminais: ainda precisam de algo (cobrança automática ou validação)")
    col_c.metric("✅ Voucher liberado", int(n_voucher))
    col_d.metric("💤 Desistiu", int(n_desistiu))

    st.markdown("---")

    # ─── Filtros ───
    categorias_disponiveis = sorted(df['_categoria'].unique().tolist())

    col_cat, col_unid, col_busca = st.columns([3, 2, 3])
    with col_cat:
        cats_selecionadas = st.multiselect(
            "📂 Categoria:",
            categorias_disponiveis,
            default=[],
            key="cliprog_cats",
            placeholder="Todas as categorias",
        )
    with col_unid:
        unid_filtro = st.radio("📍 Unidade:", ["Todas", "Mogi", "Suzano"],
            horizontal=True, key="cliprog_unid")
    with col_busca:
        busca = st.text_input("🔍 Buscar:", placeholder="Nome ou telefone",
            key="cliprog_busca")

    # Aplica filtros
    df_f = df.copy()
    if cats_selecionadas:
        df_f = df_f[df_f['_categoria'].isin(cats_selecionadas)]
    if unid_filtro != "Todas" and 'Unidade' in df_f.columns:
        df_f = df_f[df_f['Unidade'].astype(str).str.lower() == unid_filtro.lower()]
    if busca.strip():
        b = busca.strip().lower()
        mask_nome = df_f['Nome'].astype(str).str.lower().str.contains(b, na=False) if 'Nome' in df_f.columns else False
        mask_tel = df_f['Telefone'].astype(str).str.contains(b, na=False) if 'Telefone' in df_f.columns else False
        df_f = df_f[mask_nome | mask_tel]

    # Ordena por horas_no_status DESC (mais antigo primeiro)
    if '_horas' in df_f.columns:
        df_f = df_f.sort_values('_horas', ascending=False)

    st.caption(f"Mostrando **{len(df_f)}** de {n_total} clientes")

    if df_f.empty:
        st.info("Nenhum cliente com esses filtros.")
        return

    # ─── Tabela display ───
    df_display = df_f.copy()
    if '_horas' in df_display.columns:
        df_display['⏱️ Tempo'] = df_display['_horas'].apply(_fmt_tempo_horas)

    # Renomeia + seleciona colunas
    col_renames = {
        '_categoria': '🚦 Status',
        'Nome': '👤 Nome',
        'Telefone': '📱 Telefone',
        'Unidade': '📍 Unidade',
        'Funcionaria': '👩 Funcionária',
        'Total Indicacoes': '📨 Indicações',
        'PRIVACIDADE': '🔐 Privacidade',
        'Voucher Liberado': '🎁 Voucher',
    }
    cols_display = ['🚦 Status', '👤 Nome', '📱 Telefone', '📍 Unidade',
                    '👩 Funcionária', '⏱️ Tempo', '📨 Indicações',
                    '🔐 Privacidade', '🎁 Voucher']
    df_display = df_display.rename(columns=col_renames)
    cols_existentes = [c for c in cols_display if c in df_display.columns]
    df_display = df_display[cols_existentes]

    # Capitaliza unidade e funcionária
    if '📍 Unidade' in df_display.columns:
        df_display['📍 Unidade'] = df_display['📍 Unidade'].astype(str).str.title()
    if '👩 Funcionária' in df_display.columns:
        df_display['👩 Funcionária'] = df_display['👩 Funcionária'].astype(str).str.title()

    # Botão export ANTES da tabela
    col_exp, _ = st.columns([2, 5])
    with col_exp:
        sufixo = (unid_filtro.lower() if unid_filtro != "Todas" else "todas")
        _xlsx_clientes_prog(df_display, sufixo)

    st.dataframe(df_display, use_container_width=True, hide_index=True, height=450)

    st.markdown("---")

    # ─── Ação por cliente ───
    st.markdown("### 🎯 Ação em cliente")
    st.caption(
        "Selecione um cliente abaixo pra ver contatos enviados ou marcar validação."
    )

    # Monta lista de opções (nome + telefone + status)
    df_f_reset = df_f.reset_index(drop=True)
    opcoes = ["— Selecione um cliente —"]
    for _, r in df_f_reset.iterrows():
        nome = str(r.get('Nome', '?'))[:30]
        tel = str(r.get('Telefone', ''))
        cat = r['_categoria']
        opcoes.append(f"{cat} | {nome} | {tel}")

    escolha = st.selectbox("Cliente:", opcoes, key="cliprog_select", label_visibility="collapsed")

    if escolha == opcoes[0]:
        return

    idx_escolhido = opcoes.index(escolha) - 1
    cli = df_f_reset.iloc[idx_escolhido]
    tel_cli = str(cli.get('Telefone', ''))
    nome_cli = str(cli.get('Nome', '?'))
    camp_id = str(cli.get('ID Campanha', ''))
    status_atual = str(cli.get(status_col, ''))
    total_ind = int(cli.get('Total Indicacoes', 0) or 0)

    # Card com info do cliente
    st.info(
        f"**{nome_cli}** — {tel_cli} — {cli['_categoria']}\n\n"
        f"Unidade: {str(cli.get('Unidade', '?')).title()} · "
        f"Funcionária: {str(cli.get('Funcionaria', '?')).title()} · "
        f"Indicações: {total_ind} · "
        f"Voucher: {cli.get('Voucher Liberado', '?')}"
    )

    # Botões de ação (varia por status)
    col_a1, col_a2, col_a3 = st.columns(3)

    # Ver contatos (sempre disponível se tiver indicações)
    with col_a1:
        if total_ind > 0:
            if st.button(f"📞 Ver {total_ind} contato{'s' if total_ind != 1 else ''}",
                         key="cliprog_ver_contatos", use_container_width=True):
                st.session_state['cliprog_mostrar_contatos'] = camp_id
        else:
            st.button("📞 Sem contatos ainda", disabled=True, use_container_width=True)

    # Validar / Invalidar (só se aguardando)
    status_upper = status_atual.upper()
    aguardando_val = status_upper == 'AGUARDANDO_VALIDACAO'

    with col_a2:
        if aguardando_val:
            if st.button("✅ Validar (libera voucher)",
                         key="cliprog_validar", type="primary", use_container_width=True):
                st.session_state['cliprog_confirma_validacao'] = ('VALIDADO', tel_cli, nome_cli)
        else:
            st.button("✅ Validar", disabled=True, use_container_width=True,
                help="Disponível só pra clientes em AGUARDANDO_VALIDACAO")

    with col_a3:
        if aguardando_val:
            if st.button("❌ Invalidar",
                         key="cliprog_invalidar", use_container_width=True):
                st.session_state['cliprog_confirma_validacao'] = ('INVALIDADO', tel_cli, nome_cli)
        else:
            st.button("❌ Invalidar", disabled=True, use_container_width=True,
                help="Disponível só pra clientes em AGUARDANDO_VALIDACAO")

    # ─── Mostrar contatos (se solicitado) ───
    if st.session_state.get('cliprog_mostrar_contatos') == camp_id and camp_id:
        st.markdown(f"#### 📞 Contatos enviados por {nome_cli}")
        with st.spinner("Buscando contatos..."):
            contatos_data = _zapi_get("contatos_cliente", campanha_id=camp_id)

        if isinstance(contatos_data, dict) and contatos_data.get("_erro"):
            st.error(contatos_data["_erro"])
        else:
            contatos = contatos_data.get("linhas", []) if isinstance(contatos_data, dict) else []
            if contatos:
                df_c = pd.DataFrame(contatos)
                # Mostra colunas úteis
                cols_c = [c for c in ['nome_indicado', 'telefone_indicado', 'status', 'motivo'] if c in df_c.columns]
                df_c_display = df_c[cols_c].rename(columns={
                    'nome_indicado': '👤 Nome',
                    'telefone_indicado': '📱 Telefone',
                    'status': '✅ Status',
                    'motivo': '📝 Motivo',
                })
                st.dataframe(df_c_display, use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum contato encontrado pra essa campanha.")

        if st.button("Fechar lista", key="cliprog_fechar_contatos"):
            st.session_state['cliprog_mostrar_contatos'] = None
            st.rerun()

    # ─── Confirmação dupla de validação ───
    pendente = st.session_state.get('cliprog_confirma_validacao')
    if pendente:
        decisao, tel_p, nome_p = pendente
        if decisao == 'VALIDADO':
            st.warning(f"⚠️ **Confirmar:** validar {nome_p} ({tel_p})? Voucher será disparado em até 5min após confirmação.")
        else:
            st.warning(f"⚠️ **Confirmar:** invalidar {nome_p} ({tel_p})? Cliente recebe nova chance ou encerra (se 2ª invalidação).")

        col_sim, col_nao, _ = st.columns([1, 1, 4])
        with col_sim:
            if st.button(f"✅ Sim, {decisao.lower()}", key="cliprog_conf_sim",
                         type="primary", use_container_width=True):
                with st.spinner("Marcando..."):
                    resp = _zapi_get("marcar_validacao", tel=tel_p, decisao=decisao)
                if isinstance(resp, dict) and resp.get("_erro"):
                    st.error(f"Falhou: {resp['_erro']}")
                else:
                    st.success(f"✅ Marcado como {decisao}! Trigger de 5min vai processar.")
                    st.session_state['cliprog_confirma_validacao'] = None
                    _zapi_get.clear()
                    st.balloons()
        with col_nao:
            if st.button("❌ Cancelar", key="cliprog_conf_nao", use_container_width=True):
                st.session_state['cliprog_confirma_validacao'] = None
                st.rerun()


def _fmt_tempo_horas(h):
    """Formata horas em string legível: '45min', '3h', '2d 4h'"""
    if pd.isna(h):
        return "—"
    if h < 1:
        return f"{int(h*60)}min"
    if h < 24:
        return f"{int(h)}h"
    dias = int(h // 24)
    horas = int(h % 24)
    return f"{dias}d {horas}h" if horas else f"{dias}d"


def _xlsx_clientes_prog(df_export, sufixo):
    """Export XLSX de clientes no programa."""
    if df_export is None or df_export.empty:
        st.download_button("📥 Exportar XLSX (sem dados)", data=b"",
            file_name="vazio.xlsx", disabled=True, key=f"exp_clip_void_{sufixo}")
        return

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        d = df_export.copy()
        for col in d.columns:
            if pd.api.types.is_datetime64_any_dtype(d[col]):
                try: d[col] = d[col].dt.tz_localize(None)
                except (TypeError, AttributeError): pass
        d.to_excel(writer, index=False, sheet_name="clientes")

    ts = datetime.now(TZ_SP).strftime("%Y%m%d-%H%M")
    fname = f"zapi_clientes_programa_{sufixo}_{ts}.xlsx"
    st.download_button(
        label=f"📥 Exportar XLSX ({len(df_export)} linhas)",
        data=buf.getvalue(),
        file_name=fname,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"exp_clip_{sufixo}",
        help=f"Baixa os {len(df_export)} clientes filtrados",
    )


# ============================================================================
# ENTRY POINTS — chamados pelo dashboard_maislaser.py dentro da tab
# ============================================================================

def render_aba_zapi_aguardando():
    """Renderiza a tela principal do robô Z-API: aguardando validação."""
    tela_zapi_aguardando_validacao()


def render_aba_zapi_ranking():
    """Renderiza a tela de ranking de funcionárias."""
    tela_zapi_ranking()


def render_aba_zapi_indicacoes():
    """Renderiza a tela de indicações com filtros e export."""
    tela_zapi_indicacoes()


def render_aba_zapi_metricas():
    """Renderiza a tela de métricas do funil Z-API."""
    tela_zapi_metricas()


def render_aba_zapi_clientes():
    """Renderiza a tela de clientes no programa."""
    tela_zapi_clientes_programa()
