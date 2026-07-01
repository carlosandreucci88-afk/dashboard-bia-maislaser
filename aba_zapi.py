"""
==============================================================================
ABA Z-API INDICAÇÕES — Robô Z-API (Apps Script v9.8)
==============================================================================
Conecta o dashboard aos endpoints do Apps Script do Z-API.
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
# CLIENTE HTTP
# ============================================================================

@st.cache_data(ttl=30, show_spinner=False)
def _zapi_get(endpoint: str, **params):
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
    if isinstance(data, dict) and data.get("_erro"):
        st.error(f"❌ {data['_erro']}" + (f" {contexto}" if contexto else ""))
        return True
    return False


# ============================================================================
# HELPERS DE FORMATAÇÃO
# ============================================================================

def _parse_iso(s):
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
    s = str(tel).strip()
    if s.startswith("55") and len(s) == 13:
        return f"+55 ({s[2:4]}) {s[4:9]}-{s[9:]}"
    return s


# ============================================================================
# HELPERS MODO MANUAL/AUTO
# ============================================================================

@st.cache_resource
def _get_supabase_zapi():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


@st.cache_data(ttl=60, show_spinner=False)
def _get_default_modo():
    data = _zapi_get("get_default_modo")
    if isinstance(data, dict) and data.get("_erro"):
        return "MANUAL"
    modo = str(data.get("modo", "MANUAL")).upper()
    return modo if modo in ("AUTO", "MANUAL") else "MANUAL"


def _set_default_modo(modo):
    resp = _zapi_action("set_default_modo", modo=modo)
    if resp.get("_erro") or resp.get("erro"):
        st.error(f"❌ Falhou: {resp.get('_erro') or resp.get('erro')}")
        return False
    _get_default_modo.clear()
    return True


@st.cache_data(ttl=30, show_spinner=False)
def _get_progresso_campanhas_bia(campanha_ids_tuple):
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
        st.toast(f"⚠️ Não consegui ler progresso Bia: {e}", icon="⚠️")
        return {}


def _meta_respostas(total_contatos):
    import math
    return max(1, math.ceil(0.3 * int(total_contatos or 0)))


# ============================================================================
# CONFIG TELEFONES DA RECEPÇÃO
# ============================================================================

@st.cache_data(ttl=15, show_spinner=False)
def _carregar_recepcao_telefones():
    try:
        sb = _get_supabase_zapi()
        result = (sb.table("configuracoes")
                  .select("recepcao_mogi_telefone, recepcao_suzano_telefone")
                  .eq("id", 1)
                  .limit(1)
                  .execute())
        if result.data and len(result.data) > 0:
            r = result.data[0]
            return (
                str(r.get("recepcao_mogi_telefone") or "").strip(),
                str(r.get("recepcao_suzano_telefone") or "").strip(),
            )
    except Exception as e:
        st.toast(f"⚠️ Falha lendo recepção: {e}", icon="⚠️")
    return ("", "")


def _salvar_recepcao_telefones(tel_mogi, tel_suzano):
    try:
        sb = _get_supabase_zapi()
        sb.table("configuracoes").update({
            "recepcao_mogi_telefone": tel_mogi,
            "recepcao_suzano_telefone": tel_suzano,
            "atualizado_em": datetime.now(TZ_SP).isoformat(),
        }).eq("id", 1).execute()
        _carregar_recepcao_telefones.clear()
        return True
    except Exception as e:
        st.error(f"❌ Falha salvando: {e}")
        return False


def _so_digitos(s):
    if not s:
        return ""
    return "".join(ch for ch in str(s) if ch.isdigit())


def _render_config_recepcao():
    tel_mogi_atual, tel_suzano_atual = _carregar_recepcao_telefones()
    status_mogi = f"✅ {tel_mogi_atual}" if tel_mogi_atual else "⚠️ não configurado"
    status_suzano = f"✅ {tel_suzano_atual}" if tel_suzano_atual else "⚠️ não configurado"

    with st.expander(
        f"📞 Telefones que recebem alertas do Disparador AUTO  ·  "
        f"Mogi: {status_mogi}  ·  Suzano: {status_suzano}",
        expanded=False,
    ):
        st.caption(
            "Quando um lead clica num botão do template, o Disparador AUTO "
            "manda um alerta via Z-API pra esses números. Um por unidade."
        )

        col_m, col_s = st.columns(2)
        with col_m:
            novo_mogi = st.text_input("📍 Mogi", value=tel_mogi_atual, key="cfg_recep_mogi",
                                        placeholder="5511999999999",
                                        help="Número com DDI + DDD, só dígitos. Ex: 5511976473948")
        with col_s:
            novo_suzano = st.text_input("📍 Suzano", value=tel_suzano_atual, key="cfg_recep_suzano",
                                          placeholder="5511999999999",
                                          help="Número com DDI + DDD, só dígitos. Ex: 5511976473948")

        novo_mogi_clean = _so_digitos(novo_mogi)
        novo_suzano_clean = _so_digitos(novo_suzano)
        mudou = (novo_mogi_clean != tel_mogi_atual) or (novo_suzano_clean != tel_suzano_atual)

        col_btn, col_info = st.columns([1, 3])
        with col_btn:
            if st.button("💾 Salvar", key="cfg_recep_salvar",
                         type="primary" if mudou else "secondary",
                         disabled=not mudou, use_container_width=True):
                if _salvar_recepcao_telefones(novo_mogi_clean, novo_suzano_clean):
                    st.toast("Telefones da recepção atualizados", icon="✅")
                    st.rerun()
        with col_info:
            if mudou:
                st.caption("⚠️ Mudanças pendentes — clica em Salvar.")


def _filtro_unidade_zapi(key_persist="_zapi_aguard_unidade_persist"):
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
    for col, label, valor in [
        (c1, "📌 Todas",  "Todas"),
        (c2, "📍 Mogi",   "Mogi"),
        (c3, "📍 Suzano", "Suzano"),
    ]:
        with col:
            if st.button(label, key=f"zapi_und_{key_persist}_{valor}",
                         type="primary" if atual == valor else "secondary",
                         use_container_width=True):
                st.session_state[key_persist] = valor
                st.rerun()

    return st.session_state[key_persist]


def _aplicar_filtro_unidade_df(df, unidade_sel, coluna="unidade"):
    if df is None or df.empty or unidade_sel == "Todas":
        return df
    if coluna not in df.columns:
        return df
    target = unidade_sel.lower()
    return df[df[coluna].astype(str).str.lower().str.contains(target, na=False)].copy()


# ============================================================================
# TELA: ⏳ AGUARDANDO VALIDAÇÃO
# ============================================================================

def tela_zapi_aguardando_validacao():
    st.markdown("## ⏳ Aguardando validação")
    st.caption(
        "Coordenadora decide o **MODO** de cada campanha:  "
        "**👤 MANUAL** = captadora liga e valida.  "
        "**🤖 AUTO** = Disparador AUTO puxa o lote e dispara templates pros indicados (1/min). "
        "Cada clique vira handoff direto pra recepção via Z-API."
    )

    modo_default_atual = _get_default_modo()

    with st.container():
        st.markdown("""<style>
            .toggle-global-box {
                background: linear-gradient(135deg, #f0f9ff 0%, #ecfeff 100%);
                border: 1px solid #bae6fd;
                border-radius: 12px;
                padding: 14px 18px;
                margin-bottom: 16px;
            }
            </style>""", unsafe_allow_html=True)
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
                format_func=lambda x: f"👤 {x} (captadora liga)" if x == "MANUAL" else f"🤖 {x} (Disparador AUTO)",
            )
            if modo_novo != modo_default_atual:
                with st.spinner("Atualizando default global..."):
                    if _set_default_modo(modo_novo):
                        st.toast(f"Default agora é {modo_novo}", icon="✅")
                        st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

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

    _render_config_recepcao()

    unid_filtro = _filtro_unidade_zapi()
    st.markdown(
        '<hr style="margin: 12px 0 18px 0; border: none; border-top: 1px solid #E5E7EB;">',
        unsafe_allow_html=True,
    )

    df = _aplicar_filtro_unidade_df(df, unid_filtro, coluna="unidade")

    if df.empty:
        st.info(f"🎉 Nada pendente em **{unid_filtro}**." if unid_filtro != "Todas" else "Nada pendente.")
        return

    camp_ids_bia = tuple(
        df[(df["modo"] == "AUTO") & df["bia_puxou_em_dt"].notna()]["campanha_id"].dropna().tolist()
    )
    progresso_por_camp = _get_progresso_campanhas_bia(camp_ids_bia)

    _marcadas = df["validacao_marcada"].isin(["VALIDADO", "INVALIDADO", "AUTO_VALIDADO_BIA", "AUTO_INVALIDADO_BIA"])
    qtd_processando = int(_marcadas.sum())
    df_ativas = df[~_marcadas]

    qtd_sem_modo = int((df_ativas["modo"] == "").sum())
    qtd_manual = int((df_ativas["modo"] == "MANUAL").sum())
    qtd_auto_puxado = int(((df_ativas["modo"] == "AUTO") & df_ativas["bia_puxou_em_dt"].notna()).sum())

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("⚠️ Sem decisão", qtd_sem_modo, help="Coordenadora ainda não escolheu MANUAL ou AUTO")
    col_m2.metric("👤 Manual", qtd_manual, help="Aguardando captadora ligar pros indicados")
    col_m3.metric("🤖 AUTO (rodando)", qtd_auto_puxado, help="Disparador AUTO já puxou o lote, contando cliques recebidos")
    col_m4.metric("⏳ Em processamento", qtd_processando, help="Já decididas, aguardando trigger 5min disparar voucher/mensagem")

    st.markdown("---")

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

    st.markdown("""<style>
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
    </style>""", unsafe_allow_html=True)

    def _ordem_prioridade(row):
        if row["modo"] == "":
            return (0, -row["horas_parado"])
        if row["modo"] == "AUTO" and row["bia_puxou_em_dt"] is not None:
            return (1, -row["horas_parado"])
        if row["modo"] == "AUTO":
            return (2, -row["horas_parado"])
        return (3, -row["horas_parado"])

    df_ativas = df_ativas.assign(
        _prio=df_ativas.apply(_ordem_prioridade, axis=1)
    ).sort_values("_prio").reset_index(drop=True)

    for _, row in df_ativas.iterrows():
        _renderizar_card_campanha(row, progresso_por_camp, modo_default_atual)


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

    if modo_atual == "":
        modo_html = '<span class="modo-vazio">⚠️ SEM DECISÃO</span>'
    elif modo_atual == "MANUAL":
        modo_html = '<span class="modo-manual">👤 MANUAL</span>'
    elif modo_atual == "AUTO" and bia_puxou is not None:
        modo_html = '<span class="modo-auto-rodando">🤖 AUTO · RODANDO</span>'
    else:
        modo_html = '<span class="modo-auto-aguarda">🤖 AUTO · AGUARDANDO PUXAR</span>'

    with st.container():
        st.markdown(f"""
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
            """, unsafe_allow_html=True)

        estado = _detectar_estado_campanha(modo_atual, bia_puxou)

        if estado == "sem_decisao":
            _render_acao_sem_decisao(camp_id, tel, nome, modo_default_atual)
        elif estado == "manual":
            _render_acao_manual(camp_id, tel, nome, bia_puxou)
        elif estado == "auto_aguardando":
            _render_acao_auto_aguardando(camp_id, tel, nome)
        elif estado == "auto_rodando":
            _render_acao_auto_rodando(camp_id, contatos, bia_puxou, progresso_por_camp)

        ver_contatos = st.toggle("👁️ Ver os 20 contatos enviados", key=f"toggle_ver_{camp_id}")
        if ver_contatos:
            _render_lista_contatos(camp_id, nome)

        st.markdown("")


def _detectar_estado_campanha(modo, bia_puxou_dt):
    if modo == "":
        return "sem_decisao"
    if modo == "MANUAL":
        return "manual"
    if modo == "AUTO" and bia_puxou_dt is None:
        return "auto_aguardando"
    if modo == "AUTO" and bia_puxou_dt is not None:
        return "auto_rodando"
    return "sem_decisao"


def _render_acao_sem_decisao(camp_id, tel, nome, modo_default_atual):
    sugestao = "AUTO" if modo_default_atual == "AUTO" else "MANUAL"

    st.markdown(f"""
        <div class="card-acao">
        <strong>⚠️ Coordenadora precisa escolher o modo:</strong>
        <span style="color: #6b7280; font-size: 12px;">  (default global: <strong>{sugestao}</strong>)</span>
        </div>
        """, unsafe_allow_html=True)

    col_m, col_a, _ = st.columns([1.2, 1.2, 2])
    with col_m:
        if st.button("👤 MANUAL (captadora liga)", key=f"set_manual_{camp_id}",
                     use_container_width=True,
                     help="Captadora liga pros indicados pra validar. Você aperta Validar/Invalidar depois."):
            _executar_set_modo(tel, "MANUAL", nome)
    with col_a:
        if st.button("🤖 AUTO (Disparador AUTO)", key=f"set_auto_{camp_id}",
                     type="primary", use_container_width=True,
                     help="Disparador AUTO dispara templates pros 20 indicados (1/min)."):
            _executar_set_modo(tel, "AUTO", nome)


def _render_acao_manual(camp_id, tel, nome, bia_puxou_dt):
    st.markdown(
        '<div class="card-acao"><strong>👤 Modo MANUAL:</strong> '
        'captadora liga pros indicados. Após contato, aperte abaixo:</div>',
        unsafe_allow_html=True,
    )

    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_a:
        btn_validar = st.button("✅ Validar", key=f"btn_val_{camp_id}",
                                 use_container_width=True,
                                 help="Marca VALIDADO. Voucher dispara automático em até 5min.")
    with col_b:
        btn_invalidar = st.button("❌ Invalidar", key=f"btn_inv_{camp_id}",
                                   use_container_width=True,
                                   help="Marca INVALIDADO. Mensagem de invalidação dispara em até 5min.")
    with col_c:
        if bia_puxou_dt is None:
            if st.button("↩️ Mudar pra AUTO", key=f"to_auto_{camp_id}",
                         use_container_width=True,
                         help="Cancela MANUAL. Disparador AUTO vai trabalhar este lote."):
                _executar_set_modo(tel, "AUTO", nome)
        else:
            st.button("↩️ Mudar pra AUTO", key=f"to_auto_disabled_{camp_id}",
                      disabled=True, use_container_width=True,
                      help="Não dá mais — Disparador AUTO já trabalhou esse lote.")

    if btn_validar or btn_invalidar:
        decisao = "VALIDADO" if btn_validar else "INVALIDADO"
        st.session_state[f"confirm_pending_{camp_id}"] = decisao

    if st.session_state.get(f"confirm_pending_{camp_id}"):
        decisao = st.session_state[f"confirm_pending_{camp_id}"]
        cor_aviso = "#dc2626" if decisao == "VALIDADO" else "#f59e0b"
        msg_aviso = (
            f"⚠️ Confirmar **{decisao}** pra **{nome}**? "
            + ("Voucher de Revitalização Facial vai disparar." if decisao == "VALIDADO"
               else "Mensagem de invalidação vai disparar.")
        )
        st.markdown(
            f"<div style='padding: 10px; background: #fff7ed; border-left: 4px solid {cor_aviso}; border-radius: 6px; margin: 8px 0;'>{msg_aviso}</div>",
            unsafe_allow_html=True,
        )
        col_sim, col_nao = st.columns([1, 1])
        with col_sim:
            confirmar = st.button("✔️ Confirmar", key=f"confirm_{camp_id}",
                                   type="primary", use_container_width=True)
        with col_nao:
            cancelar = st.button("✖️ Cancelar", key=f"cancel_{camp_id}", use_container_width=True)

        if cancelar:
            st.session_state.pop(f"confirm_pending_{camp_id}", None)
            st.rerun()

        if confirmar:
            with st.spinner(f"Marcando {decisao}..."):
                resp = _zapi_action("marcar_validacao", tel=tel, decisao=decisao, modo="MANUAL")
            if resp.get("_erro") or resp.get("erro"):
                st.error(f"❌ Falhou: {resp.get('_erro') or resp.get('erro')}")
            elif resp.get("ja_marcado"):
                st.warning(f"ℹ️ Já estava marcado como {decisao} (alguém adiantou).")
                st.session_state.pop(f"confirm_pending_{camp_id}", None)
                _zapi_get.clear()
            else:
                st.success(f"✅ {decisao} marcado! Trigger vai processar em até 5min.")
                st.session_state.pop(f"confirm_pending_{camp_id}", None)
                _zapi_get.clear()
                st.balloons()
            st.rerun()


def _render_acao_auto_aguardando(camp_id, tel, nome):
    st.markdown(
        '<div class="card-acao">'
        '<strong>🤖 Modo AUTO selecionado.</strong> '
        'Disparador AUTO vai puxar este lote no próximo ciclo (a cada 10min). '
        'Depois dispara 1 template por minuto até esvaziar a fila.'
        '<br><br>'
        '<small>Você ainda pode voltar pra MANUAL enquanto o lote não for puxado.</small>'
        '</div>',
        unsafe_allow_html=True,
    )

    col_a, _ = st.columns([1.5, 3])
    with col_a:
        if st.button("↩️ Mudar pra MANUAL", key=f"to_manual_{camp_id}",
                     use_container_width=True,
                     help="Cancela AUTO. Captadora vai ter que ligar manualmente."):
            _executar_set_modo(tel, "MANUAL", nome)


def _render_acao_auto_rodando(camp_id, contatos, bia_puxou_dt, progresso_por_camp):
    cliques = progresso_por_camp.get(camp_id, 0)
    pct = int(min(100, (cliques / contatos * 100) if contatos > 0 else 0))
    agora = datetime.now(TZ_SP)
    horas_rodando = (agora - bia_puxou_dt).total_seconds() / 3600

    st.markdown(f"""
        <div class="card-acao">
        <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
            <div>
                🤖 <strong>Disparador AUTO trabalhando há {horas_rodando:.1f}h</strong>
            </div>
            <div style="color: #6b7280; font-size: 12px;">
                {contatos} indicados
            </div>
        </div>
        <div style="margin-top: 8px;">
            💬 <strong>Cliques recebidos:</strong> {cliques} / {contatos} ({pct}%)
            <div class="progress-bg">
                <div class="progress-fill" style="width: {pct}%;"></div>
            </div>
        </div>
        <div style="margin-top: 10px; font-size: 12px; color: #6b7280;">
            ℹ️ Cada clique de botão (AGENDAR / SABER MAIS) gera alerta automático
            pra recepção via Z-API. Coordenadora não precisa fazer nada aqui.
        </div>
        </div>
        """, unsafe_allow_html=True)


def _executar_set_modo(tel, modo, nome):
    with st.spinner(f"Definindo modo {modo} pra {nome}..."):
        resp = _zapi_action("set_modo_campanha", tel=tel, modo=modo)

    if resp.get("_erro") or resp.get("erro"):
        st.error(f"❌ Falhou: {resp.get('_erro') or resp.get('erro')}")
        return

    if modo == "AUTO":
        st.toast(f"🤖 AUTO marcado pra {nome}. Disparador puxa em até 10min.", icon="🚀")
    else:
        st.toast(f"Modo {modo} aplicado pra {nome}", icon="✅")

    _zapi_get.clear()
    _get_progresso_campanhas_bia.clear()
    st.rerun()


def _render_lista_contatos(camp_id, nome):
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

@st.cache_data(ttl=300, show_spinner=False)
def _zapi_get_ranking(data_inicio: str = "", data_fim: str = ""):
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente"}

    params = {"endpoint": "funcionarias_real", "token": token}
    if data_inicio:
        params["data_inicio"] = data_inicio
    if data_fim:
        params["data_fim"] = data_fim

    try:
        resp = requests.get(url, params=params, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            return {"_erro": f"HTTP {resp.status_code} ao calcular ranking"}
        data = resp.json()
        if isinstance(data, dict) and data.get("erro"):
            return {"_erro": f"Z-API: {data['erro']}"}
        return data
    except requests.exceptions.Timeout:
        return {"_erro": "Apps Script demorou demais (>30s)."}
    except requests.exceptions.RequestException as e:
        return {"_erro": f"Erro de rede: {e}"}
    except ValueError:
        return {"_erro": "Resposta não é JSON válido."}


def tela_zapi_ranking():
    st.markdown("## 🏆 Ranking de funcionárias")
    st.caption(
        "Calculado em tempo real a partir de CLIENTES + arquivo + INDICACOES + arquivo. "
        "Conta como **cliente** quem bateu meta. "
        "Conta como **indicação** cada contato indicado por essas clientes."
    )

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
        preset_escolhido = st.selectbox("Período:", list(PRESETS.keys()), index=0, key="rank_periodo_preset")
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
            di = st.date_input("Data início:", value=hoje - timedelta(days=30),
                               max_value=hoje, key="rank_data_inicio", format="DD/MM/YYYY")
        with col_df:
            df_data = st.date_input("Data fim:", value=hoje, max_value=hoje,
                                     key="rank_data_fim", format="DD/MM/YYYY")
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

    linhas = data.get("linhas", [])
    if not linhas:
        st.warning("Nenhum dado no ranking ainda.")
        return

    df = pd.DataFrame(linhas)

    if "disparos" in df.columns:
        df = df.rename(columns={"disparos": "clientes_com_indicacoes"})
    df["indic_por_cliente"] = df.apply(
        lambda r: round(r["indicacoes_validas"] / r["clientes_com_indicacoes"], 1)
                  if r["clientes_com_indicacoes"] > 0 else 0,
        axis=1,
    )

    unid_filtro = st.radio("Filtrar por unidade:", ["Todas", "Mogi", "Suzano"],
                            horizontal=True, key="rank_unid_filtro")
    df_filtrado = df.copy()
    if unid_filtro != "Todas":
        df_filtrado = df_filtrado[df_filtrado["unidade"].str.lower() == unid_filtro.lower()]

    n_func = len(df_filtrado)
    n_cli = int(df_filtrado["clientes_com_indicacoes"].sum())
    n_ind = int(df_filtrado["indicacoes_validas"].sum())
    n_vouch = int(df_filtrado["vouchers_validados"].sum()) if "vouchers_validados" in df_filtrado.columns else 0

    n_ind_fmt = f"{n_ind:,}".replace(",", ".")

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("👥 Funcionárias", n_func, help="Funcionárias com pelo menos 1 cliente ou indicação no período")
    col_b.metric("🎯 Bateram meta", n_cli, help="Clientes que enviaram 20 contatos válidos")
    col_c.metric("📨 Indicações", n_ind_fmt, help="Total de contatos indicados pelas clientes")
    col_d.metric("🎁 Vouchers", n_vouch, help="Clientes que tiveram voucher liberado")

    st.markdown("---")

    df = df_filtrado

    if df.empty:
        st.info(f"Nenhuma funcionária em {unid_filtro}.")
        return

    df = df.sort_values("indicacoes_validas", ascending=False).reset_index(drop=True)

    st.markdown("### 🥇 Top 5")
    top5 = df.head(5)
    medalhas = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    cols = st.columns(min(5, len(top5)))
    for i, (_, r) in enumerate(top5.iterrows()):
        with cols[i]:
            n_ind_val = int(r['indicacoes_validas'])
            n_ind_str = f"{n_ind_val:,}".replace(",", ".")
            st.markdown(f"""
                <div style="padding: 14px; border: 1px solid #e5e7eb; border-radius: 10px;
                            text-align: center; background: #fafafa; min-height: 150px;">
                  <div style="font-size: 28px;">{medalhas[i]}</div>
                  <div style="font-weight: 700; font-size: 14px; margin-top: 4px;">{r['funcionaria']}</div>
                  <div style="color: #6b7280; font-size: 12px;">{r['unidade']}</div>
                  <div style="margin-top: 8px; font-size: 22px; font-weight: 700; color: #059669;">{n_ind_str}</div>
                  <div style="color: #6b7280; font-size: 11px;">indicações</div>
                  <div style="margin-top: 4px; font-size: 12px; color: #374151;">
                    {int(r['clientes_com_indicacoes'])} cliente(s) c/ meta
                  </div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("---")

    st.markdown("### 📋 Ranking completo")
    df_tabela = df.copy()
    df_tabela.insert(0, "#", range(1, len(df_tabela) + 1))

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

    st.dataframe(df_tabela, use_container_width=True, hide_index=True, column_config=column_config)

    st.markdown("---")

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

    st.caption(f"📅 Calculado em {data.get('gerado_em', '—')} · Cache 5min")


# ============================================================================
# TELA: 📨 INDICAÇÕES
# ============================================================================

@st.cache_data(ttl=120, show_spinner=False)
def _zapi_get_indicacoes(data_inicio: str = "", data_fim: str = "",
                          incluir_arquivo: bool = False,
                          busca: str = "", status: str = "",
                          unidade: str = "", funcionaria: str = "",
                          limit: int = 5000):
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente"}

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
        return {"_erro": "Apps Script demorou demais (>45s)."}
    except requests.exceptions.RequestException as e:
        return {"_erro": f"Erro de rede: {e}"}
    except ValueError:
        return {"_erro": "Resposta não é JSON válido."}


def _xlsx_indicacoes(df_export, sufixo_arquivo):
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
                try:
                    d[col] = d[col].dt.tz_localize(None)
                except (TypeError, AttributeError):
                    pass
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
    st.caption("Cada linha é um contato indicado por um cliente. Inclua o arquivo pra ver histórico completo.")

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

    col_b, col_u, col_f = st.columns([3, 2, 2])
    with col_b:
        busca = st.text_input("🔍 Buscar:", placeholder="Nome ou telefone (cliente ou indicado)", key="ind_busca")
    with col_u:
        unid_filtro = st.radio("Unidade:", ["Todas", "Mogi", "Suzano"], horizontal=True, key="ind_unid")
    with col_f:
        func_filtro = st.text_input("Funcionária:", placeholder="Ex: rafaela", key="ind_func")

    unidade_str = "" if unid_filtro == "Todas" else unid_filtro.lower()

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

    base_total = total_planilha + (total_arquivo if incluir_arq else 0)
    total_filtrado_fmt = f"{total_filtrado:,}".replace(",", ".")
    base_total_fmt = f"{base_total:,}".replace(",", ".")
    n_linhas_fmt = f"{len(linhas):,}".replace(",", ".")

    col_a, col_b_card, col_c, col_d = st.columns(4)
    col_a.metric("📨 Filtradas", total_filtrado_fmt)
    col_b_card.metric("📊 Base total", base_total_fmt,
                       help=f"INDICACOES atual: {total_planilha}\n" +
                            (f"INDICACOES_ARQUIVO: {total_arquivo}" if incluir_arq else "(arquivo não incluído)"))
    col_c.metric("👁️ Mostrando", n_linhas_fmt, help=f"Limite por chamada: {limit_aplicado}.")
    col_d.metric("📦 Arquivo", "Incluído" if incluir_arq else "Não incluído")

    if total_filtrado > len(linhas):
        st.warning(f"⚠️ {total_filtrado_fmt} indicações no filtro, mas só {n_linhas_fmt} exibidas (limite {limit_aplicado}). Refine ou use XLSX.")

    if not linhas:
        st.info("Nenhuma indicação encontrada com os filtros atuais.")
        return

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
# TELA: 📊 MÉTRICAS Z-API (contrato flat + fix barras funil)
# ============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _zapi_get_metricas(data_inicio: str = "", data_fim: str = ""):
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente"}

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
        return {"_erro": "Resposta não é JSON válido."}


def tela_zapi_metricas():
    st.markdown("## 📊 Métricas Z-API — Funil & Conversão")
    st.caption(
        "Visão do funil de conversão do Indique e Ganhe: onde os clientes "
        "convertem e onde travam. Cálculo em tempo real a partir de CLIENTES + arquivo."
    )

    from datetime import date, timedelta
    hoje = date.today()

    col_tog, col_di, col_df, col_btn = st.columns([2, 2, 2, 1])
    with col_tog:
        usar_filtro = st.toggle("🎯 Filtrar por período", value=False, key="met_usar_filtro",
                                 help="Filtra por Data Cadastro")

    data_inicio_str = ""
    data_fim_str = ""

    if usar_filtro:
        with col_di:
            di = st.date_input("Data início:", value=hoje - timedelta(days=30),
                               max_value=hoje, key="met_di", format="DD/MM/YYYY")
        with col_df:
            df_data = st.date_input("Data fim:", value=hoje, max_value=hoje,
                                     key="met_df", format="DD/MM/YYYY")
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

    if usar_filtro:
        di_fmt = "/".join(reversed(data_inicio_str.split("-")))
        df_fmt = "/".join(reversed(data_fim_str.split("-")))
        st.caption(f"📍 Período: **{di_fmt}** até **{df_fmt}**")

    with st.spinner("Calculando métricas do funil..."):
        data = _zapi_get_metricas(data_inicio_str, data_fim_str)

    if _mostrar_erro_e_parar(data, "(carregando métricas)"):
        return

    funil = data.get("funil", {}) or {}

    n_convidados = int(funil.get("iniciaram_conversa", 0) or 0)
    n_priv       = int(funil.get("escolheram_privacidade", 0) or 0)
    n_enviou1    = int(funil.get("enviaram_pelo_menos_1", 0) or 0)
    n_bateu_meta = int(funil.get("bateram_meta", 0) or 0)
    n_validados  = int(funil.get("validados", 0) or 0)
    n_invalid    = int(funil.get("invalidados", 0) or 0)
    n_desistiu   = int(funil.get("encerrados_sem_resposta", 0) or 0)
    n_andamento  = int(funil.get("em_andamento", 0) or 0)

    def _pct(numerador, denominador):
        if not denominador:
            return 0.0
        return round(numerador / denominador * 100, 1)

    pct_priv     = _pct(n_priv, n_convidados)
    pct_enviou1  = _pct(n_enviou1, n_convidados)
    pct_meta     = _pct(n_bateu_meta, n_convidados)
    pct_voucher  = _pct(n_validados, n_convidados)

    # ─── 4 CARDS MACRO ───
    n_convidados_fmt = f"{n_convidados:,}".replace(",", ".")
    n_validados_fmt = f"{n_validados:,}".replace(",", ".")
    n_andamento_fmt = f"{n_andamento:,}".replace(",", ".")
    n_desistiu_fmt = f"{n_desistiu:,}".replace(",", ".")

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("👥 Convidados", n_convidados_fmt,
                  help="Total de clientes que iniciaram conversa")
    col_b.metric("🎁 Voucher liberado", n_validados_fmt,
                  delta=f"{pct_voucher}% conversão", delta_color="normal")
    col_c.metric("🚀 Em andamento", n_andamento_fmt,
                  help="Ainda não terminaram o funil")
    col_d.metric("💤 Desistiram", n_desistiu_fmt,
                  delta=f"-{_pct(n_desistiu, n_convidados)}%",
                  delta_color="inverse",
                  help="Pararam de responder após cobrança")

    st.markdown("---")

    # ─── FUNIL VISUAL (4 barras) — FIX: n_fmt calculado antes, sem .replace no HTML ───
    st.markdown("### 🔻 Funil de conversão")

    niveis = [
        ("👥 Convidados",             n_convidados, 100.0 if n_convidados else 0.0),
        ("✅ Escolheu privacidade",   n_priv,       pct_priv),
        ("🎯 Bateu meta (20)",        n_bateu_meta, pct_meta),
        ("🎁 Recebeu voucher",        n_validados,  pct_voucher),
    ]

    for label, n, pct in niveis:
        n_fmt = f"{n:,}".replace(",", ".")
        st.markdown(
            f"""<div style="margin: 8px 0;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                    <strong>{label}</strong>
                    <span style="color: #5BC0BE; font-weight: 700;">{n_fmt} ({pct}%)</span>
                </div>
                <div style="background: #e5e7eb; border-radius: 8px; height: 28px; overflow: hidden;">
                    <div style="background: linear-gradient(90deg, #5BC0BE 0%, #4AA8A6 100%);
                                width: {pct}%; height: 100%; border-radius: 8px;
                                transition: width 0.6s ease;"></div>
                </div>
            </div>""",
            unsafe_allow_html=True
        )

    st.markdown("---")

    # ─── STATUS DETALHADO ───
    st.markdown("### 📊 Distribuição por status final")

    col_v, col_i, col_d_col, col_and = st.columns(4)
    col_v.metric("✅ Validados", n_validados, help="STATUS_REC = FINALIZADO")
    col_i.metric("❌ Invalidados", n_invalid,
                  help="STATUS_REC = INVALIDADO_AVISADO ou INVALIDADO_COBRADO",
                  delta=f"{_pct(n_invalid, n_convidados)}%",
                  delta_color="inverse" if n_invalid > 0 else "off")
    col_d_col.metric("💤 Sem resposta", n_desistiu,
                      delta=f"{_pct(n_desistiu, n_convidados)}%",
                      delta_color="inverse" if n_desistiu > 0 else "off",
                      help="_COBRADOSEMRESPOSTA")
    col_and.metric("🚀 Ainda em andamento", n_andamento,
                    help="Aguardando privacidade, contatos ou validação")

    st.markdown("---")

    # ─── TAXAS ───
    st.markdown("### 📈 Taxas de conversão")
    col_t1, col_t2, col_t3, col_t4 = st.columns(4)
    col_t1.metric("🔐 % escolheu privacidade", f"{pct_priv}%")
    col_t2.metric("📨 % enviou pelo menos 1", f"{pct_enviou1}%")
    col_t3.metric("🎯 % bateu meta", f"{pct_meta}%")
    col_t4.metric("🎁 % conversão total", f"{pct_voucher}%")

    # ─── Footer ───
    gerado = data.get("gerado_em", "")
    if gerado:
        try:
            from datetime import datetime as _dt
            gerado_dt = _dt.fromisoformat(gerado.replace("Z", "+00:00")).astimezone(TZ_SP)
            gerado_fmt = gerado_dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            gerado_fmt = gerado
        st.caption(f"📅 Calculado em {gerado_fmt} · Cache 5min")
    else:
        st.caption("Cache 5min. Clica em 🔄 Atualizar pra forçar refresh.")


# ============================================================================
# TELA: 👥 CLIENTES NO PROGRAMA
# ============================================================================

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
    s = str(status).upper() if status else ''
    return s not in ('FINALIZADO', 'ENCERRADO', '_COBRADOSEMRESPOSTA')


def tela_zapi_clientes_programa():
    st.markdown("## 👥 Clientes no programa")
    st.caption("Todos os clientes em CLIENTES (atual). Ordenado por tempo no status atual.")

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

    df = pd.DataFrame(linhas)

    if 'Funcionaria' in df.columns:
        df = df[df['Funcionaria'].astype(str).str.lower().str.strip() != 'teste']

    if df.empty:
        st.info("Nenhum cliente real (só 'teste').")
        return

    status_col = 'STATUS DE AONDE PAROU' if 'STATUS DE AONDE PAROU' in df.columns else 'status_rec'
    df['_categoria'] = df[status_col].apply(_categoria_cliente)
    df['_ativo'] = df[status_col].apply(_eh_ativo)

    if 'DATA E HORA' in df.columns:
        df['_data_hora'] = pd.to_datetime(df['DATA E HORA'], errors='coerce', utc=True).dt.tz_convert(TZ_SP)
        agora = datetime.now(TZ_SP)
        df['_horas'] = (agora - df['_data_hora']).dt.total_seconds() / 3600

    n_total = len(df)
    n_ativos = df['_ativo'].sum()
    n_voucher = (df.get('Voucher Liberado', '').astype(str).str.upper() == 'SIM').sum() if 'Voucher Liberado' in df.columns else 0
    n_desistiu = (df[status_col] == '_COBRADOSEMRESPOSTA').sum()

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("👥 Total em CLIENTES", n_total)
    col_b.metric("🔵 Em ação ativa", int(n_ativos),
                  help="Não-terminais: ainda precisam de algo")
    col_c.metric("✅ Voucher liberado", int(n_voucher))
    col_d.metric("💤 Desistiu", int(n_desistiu))

    st.markdown("---")

    categorias_disponiveis = sorted(df['_categoria'].unique().tolist())

    col_cat, col_unid, col_busca = st.columns([3, 2, 3])
    with col_cat:
        cats_selecionadas = st.multiselect("📂 Categoria:", categorias_disponiveis, default=[],
                                            key="cliprog_cats", placeholder="Todas as categorias")
    with col_unid:
        unid_filtro = st.radio("📍 Unidade:", ["Todas", "Mogi", "Suzano"], horizontal=True, key="cliprog_unid")
    with col_busca:
        busca = st.text_input("🔍 Buscar:", placeholder="Nome ou telefone", key="cliprog_busca")

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

    if '_horas' in df_f.columns:
        df_f = df_f.sort_values('_horas', ascending=False)

    st.caption(f"Mostrando **{len(df_f)}** de {n_total} clientes")

    if df_f.empty:
        st.info("Nenhum cliente com esses filtros.")
        return

    df_display = df_f.copy()
    if '_horas' in df_display.columns:
        df_display['⏱️ Tempo'] = df_display['_horas'].apply(_fmt_tempo_horas)

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

    if '📍 Unidade' in df_display.columns:
        df_display['📍 Unidade'] = df_display['📍 Unidade'].astype(str).str.title()
    if '👩 Funcionária' in df_display.columns:
        df_display['👩 Funcionária'] = df_display['👩 Funcionária'].astype(str).str.title()

    col_exp, _ = st.columns([2, 5])
    with col_exp:
        sufixo = (unid_filtro.lower() if unid_filtro != "Todas" else "todas")
        _xlsx_clientes_prog(df_display, sufixo)

    st.dataframe(df_display, use_container_width=True, hide_index=True, height=450)

    st.markdown("---")

    st.markdown("### 🎯 Ação em cliente")
    st.caption("Selecione um cliente abaixo pra ver contatos enviados ou marcar validação.")

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

    st.info(
        f"**{nome_cli}** — {tel_cli} — {cli['_categoria']}\n\n"
        f"Unidade: {str(cli.get('Unidade', '?')).title()} · "
        f"Funcionária: {str(cli.get('Funcionaria', '?')).title()} · "
        f"Indicações: {total_ind} · "
        f"Voucher: {cli.get('Voucher Liberado', '?')}"
    )

    col_a1, col_a2, col_a3 = st.columns(3)

    with col_a1:
        if total_ind > 0:
            if st.button(f"📞 Ver {total_ind} contato{'s' if total_ind != 1 else ''}",
                         key="cliprog_ver_contatos", use_container_width=True):
                st.session_state['cliprog_mostrar_contatos'] = camp_id
        else:
            st.button("📞 Sem contatos ainda", disabled=True, use_container_width=True)

    status_upper = status_atual.upper()
    aguardando_val = status_upper == 'AGUARDANDO_VALIDACAO'

    with col_a2:
        if aguardando_val:
            if st.button("✅ Validar (libera voucher)", key="cliprog_validar",
                         type="primary", use_container_width=True):
                st.session_state['cliprog_confirma_validacao'] = ('VALIDADO', tel_cli, nome_cli)
        else:
            st.button("✅ Validar", disabled=True, use_container_width=True,
                      help="Disponível só pra clientes em AGUARDANDO_VALIDACAO")

    with col_a3:
        if aguardando_val:
            if st.button("❌ Invalidar", key="cliprog_invalidar", use_container_width=True):
                st.session_state['cliprog_confirma_validacao'] = ('INVALIDADO', tel_cli, nome_cli)
        else:
            st.button("❌ Invalidar", disabled=True, use_container_width=True,
                      help="Disponível só pra clientes em AGUARDANDO_VALIDACAO")

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
    if df_export is None or df_export.empty:
        st.download_button("📥 Exportar XLSX (sem dados)", data=b"",
                           file_name="vazio.xlsx", disabled=True, key=f"exp_clip_void_{sufixo}")
        return

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        d = df_export.copy()
        for col in d.columns:
            if pd.api.types.is_datetime64_any_dtype(d[col]):
                try:
                    d[col] = d[col].dt.tz_localize(None)
                except (TypeError, AttributeError):
                    pass
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
# ENTRY POINTS
# ============================================================================

def render_aba_zapi_aguardando():
    tela_zapi_aguardando_validacao()


def render_aba_zapi_ranking():
    tela_zapi_ranking()


def render_aba_zapi_indicacoes():
    tela_zapi_indicacoes()


def render_aba_zapi_metricas():
    tela_zapi_metricas()


def render_aba_zapi_clientes():
    tela_zapi_clientes_programa()
