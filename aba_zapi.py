"""
==============================================================================
ABA Z-API INDICAÇÕES — Robô Z-API (Apps Script v9.1)
==============================================================================
Conecta o dashboard aos endpoints read-only + ação do Apps Script do Z-API:

  GET endpoints (leitura):
    /?endpoint=ping              → healthcheck
    /?endpoint=clientes          → todas as linhas de CLIENTES
    /?endpoint=indicacoes&limit  → últimas N indicações
    /?endpoint=validacao         → pendentes de validação enriquecidas
    /?endpoint=contatos_cliente&campanha_id=ID → 20 contatos da campanha
    /?endpoint=funcionarias      → ranking
    /?endpoint=stats             → métricas agregadas leves

  AÇÃO (também GET, com query params):
    /?endpoint=marcar_validacao&tel=...&decisao=VALIDADO|INVALIDADO
      → marca o dropdown na aba certa. O trigger processarValidacoes (5min)
        que já existe no Apps Script é quem dispara voucher / mensagem.

v1 (08/06/2026): tela ⏳ Aguardando validação (operacional, com botões).
                 Outras telas (clientes, indicações, ranking, métricas) vêm depois.
==============================================================================
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone

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
    Versão NÃO cacheada do _zapi_get, para AÇÕES (marcar_validacao).
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
# TELA: ⏳ AGUARDANDO VALIDAÇÃO
# ============================================================================

def tela_zapi_aguardando_validacao():
    st.markdown("## ⏳ Aguardando validação")
    st.caption("Clientes que enviaram os 20 contatos e estão esperando captadora ligar e validar. "
               "Após marcar Validado/Invalidado aqui, o trigger do Apps Script (a cada 5min) "
               "dispara o voucher ou a mensagem de invalidação automaticamente.")

    data = _zapi_get("validacao")
    if _mostrar_erro_e_parar(data, "(carregando pendências)"):
        return

    linhas = data.get("linhas", [])
    if not linhas:
        st.success("🎉 Nada na fila! Todas as validações estão em dia.")
        return

    # Cards de resumo no topo
    df = pd.DataFrame(linhas)
    df["data_hora_dt"] = df["data_hora"].apply(_parse_iso)
    df["horas_parado"] = df["data_hora_dt"].apply(
        lambda d: ((datetime.now(TZ_SP) - d).total_seconds() / 3600) if d else 0
    )

    qtd_total = len(df)
    qtd_urgente = int((df["horas_parado"] >= 24).sum())
    qtd_atencao = int(((df["horas_parado"] >= 12) & (df["horas_parado"] < 24)).sum())
    qtd_mogi = int((df["unidade"].str.lower() == "mogi").sum())
    qtd_suzano = int((df["unidade"].str.lower() == "suzano").sum())

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("📋 Total na fila", qtd_total)
    col_m2.metric("🔴 Urgente (24h+)", qtd_urgente)
    col_m3.metric("🟡 Atenção (12-24h)", qtd_atencao)
    col_m4.metric("📍 Mogi / Suzano", f"{qtd_mogi} / {qtd_suzano}")

    st.markdown("---")

    # Filtro por unidade
    unidades = ["Todas", "Mogi", "Suzano"]
    unid_filtro = st.radio(
        "Filtrar por unidade:",
        unidades,
        horizontal=True,
        key="zapi_aguard_unidade",
    )
    if unid_filtro != "Todas":
        df = df[df["unidade"].str.lower() == unid_filtro.lower()]

    if df.empty:
        st.info(f"Nada pendente em {unid_filtro}.")
        return

    # Ordena: mais urgente primeiro
    df = df.sort_values("horas_parado", ascending=False).reset_index(drop=True)

    st.markdown(f"### {len(df)} cliente(s) aguardando")

    # CSS local pros badges
    st.markdown("""
    <style>
    .urg-urgente { background: #fee2e2; color: #991b1b; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .urg-atencao { background: #fef3c7; color: #92400e; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .urg-ok      { background: #dcfce7; color: #166534; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 12px; }
    .priv-anonimo      { background: #f3e8ff; color: #6b21a8; padding: 1px 8px; border-radius: 8px; font-size: 11px; }
    .priv-identificado { background: #dbeafe; color: #1e40af; padding: 1px 8px; border-radius: 8px; font-size: 11px; }
    .priv-vazia        { background: #f3f4f6; color: #6b7280; padding: 1px 8px; border-radius: 8px; font-size: 11px; }
    </style>
    """, unsafe_allow_html=True)

    # Lista de cards (1 por cliente pendente)
    for _, row in df.iterrows():
        tel = row["telefone"]
        nome = row["nome"] or "(sem nome)"
        func = row["funcionaria"] or "—"
        unid = row["unidade"] or "—"
        contatos = int(row["contatos"] or 0)
        priv = (row.get("privacidade") or "").upper()
        camp_id = row["campanha_id"]
        dt = row["data_hora_dt"]
        tempo = _humanizar_tempo(dt)
        urg = _classe_urgencia(dt)

        urg_label = {"urgente": "🔴 URGENTE", "atencao": "🟡 ATENÇÃO", "ok": "🟢 OK"}[urg]
        priv_label = {"ANONIMO": "🤫 anônima", "IDENTIFICADO": "✨ identificada"}.get(priv, "— sem privacidade")
        priv_class = {"ANONIMO": "priv-anonimo", "IDENTIFICADO": "priv-identificado"}.get(priv, "priv-vazia")

        with st.container():
            st.markdown(
                f"""
                <div style="padding: 12px 14px; border: 1px solid #e5e7eb; border-radius: 10px; margin-bottom: 8px;">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <div>
                      <span style="font-size: 16px; font-weight: 700;">{nome}</span>
                      &nbsp;<span class="{priv_class}">{priv_label}</span>
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

            col_a, col_b, col_c = st.columns([1, 1, 1.6])
            with col_a:
                btn_validar = st.button(
                    "✅ Validar",
                    key=f"btn_val_{camp_id}",
                    use_container_width=True,
                    help="Marca VALIDADO na planilha. Voucher dispara automático em até 5min."
                )
            with col_b:
                btn_invalidar = st.button(
                    "❌ Invalidar",
                    key=f"btn_inv_{camp_id}",
                    use_container_width=True,
                    help="Marca INVALIDADO. Mensagem de invalidação dispara em até 5min."
                )
            with col_c:
                ver_contatos = st.toggle(
                    "👁️ Ver os contatos",
                    key=f"toggle_ver_{camp_id}",
                )

            # Confirmação dupla: precisa marcar checkbox antes de o botão funcionar
            # (evita clique acidental que libera voucher real)
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
                    confirmar = st.button("✔️ Confirmar", key=f"confirm_{camp_id}", type="primary", use_container_width=True)
                with col_nao:
                    cancelar = st.button("✖️ Cancelar", key=f"cancel_{camp_id}", use_container_width=True)

                if cancelar:
                    st.session_state.pop(f"confirm_pending_{camp_id}", None)
                    st.rerun()

                if confirmar:
                    with st.spinner(f"Marcando {decisao}..."):
                        resp = _zapi_action("marcar_validacao", tel=tel, decisao=decisao)
                    if resp.get("_erro") or resp.get("erro"):
                        st.error(f"❌ Falhou: {resp.get('_erro') or resp.get('erro')}")
                    elif resp.get("ja_marcado"):
                        st.warning(f"ℹ️ Já estava marcado como {decisao} (alguém adiantou).")
                        st.session_state.pop(f"confirm_pending_{camp_id}", None)
                        _zapi_get.clear()
                    else:
                        st.success(f"✅ {decisao} marcado! Trigger vai processar em até 5min e disparar a mensagem pra cliente.")
                        st.session_state.pop(f"confirm_pending_{camp_id}", None)
                        _zapi_get.clear()
                        st.balloons()
                    # Pequena pausa visual e refresh
                    st.rerun()

            # Bloco expansível com os 20 contatos
            if ver_contatos:
                with st.spinner(f"Carregando contatos da {nome}..."):
                    contatos_data = _zapi_get("contatos_cliente", campanha_id=camp_id)
                if _mostrar_erro_e_parar(contatos_data, "(carregando contatos)"):
                    pass
                else:
                    contatos_lista = contatos_data.get("linhas", [])
                    if not contatos_lista:
                        st.info("Nenhum contato encontrado nessa campanha (estranho).")
                    else:
                        df_c = pd.DataFrame(contatos_lista)
                        df_c["telefone_formatado"] = df_c["telefone_indicado"].apply(_formatar_telefone)
                        df_c = df_c[["nome_indicado", "telefone_formatado"]].rename(
                            columns={"nome_indicado": "Nome", "telefone_formatado": "Telefone"}
                        )
                        st.dataframe(df_c, use_container_width=True, hide_index=True)
                        st.caption(f"📋 {len(contatos_lista)} contatos indicados pela cliente")

            st.markdown("")  # respiro entre cards


# ============================================================================
# ENTRY POINT — chamado pelo dashboard_maislaser.py dentro da tab
# ============================================================================

def render_aba_zapi_aguardando():
    """Renderiza a tela principal do robô Z-API: aguardando validação."""
    tela_zapi_aguardando_validacao()
