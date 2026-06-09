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
    # Separa quem já foi marcado (processando) de quem ainda precisa da captadora
    _marcadas = df["validacao_marcada"].fillna("").astype(str).str.upper().isin(["VALIDADO", "INVALIDADO"])
    qtd_processando = int(_marcadas.sum())
    df_para_contar = df[~_marcadas]
    qtd_urgente = int((df_para_contar["horas_parado"] >= 24).sum())
    qtd_atencao = int(((df_para_contar["horas_parado"] >= 12) & (df_para_contar["horas_parado"] < 24)).sum())
    qtd_mogi = int((df_para_contar["unidade"].str.lower() == "mogi").sum())
    qtd_suzano = int((df_para_contar["unidade"].str.lower() == "suzano").sum())

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("📋 Aguardando captadora", len(df_para_contar),
                  help=f"{qtd_total} no total ({qtd_processando} já marcadas, em processamento)")
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

    # Separa: já marcados (aguardando trigger processar) × ainda precisam de captadora
    df["validacao_marcada"] = df["validacao_marcada"].fillna("").astype(str).str.upper()
    df_processando = df[df["validacao_marcada"].isin(["VALIDADO", "INVALIDADO"])].copy()
    df_aguardando = df[~df["validacao_marcada"].isin(["VALIDADO", "INVALIDADO"])].copy()

    # Banner em cima se tem alguém processando
    if not df_processando.empty:
        nomes_proc = ", ".join(df_processando["nome"].tolist()[:5])
        extras = f" e mais {len(df_processando) - 5}" if len(df_processando) > 5 else ""
        st.info(
            f"⏳ **{len(df_processando)} cliente(s) processando:** {nomes_proc}{extras}. "
            f"O trigger do Apps Script vai disparar voucher/mensagem em até 5min e elas saem desta lista."
        )

    if df_aguardando.empty:
        st.success("🎉 Sem pendências pra captadora atuar agora.")
        return

    st.markdown(f"### {len(df_aguardando)} cliente(s) aguardando captadora")

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

    # Lista de cards (1 por cliente pendente — só os que ainda precisam de ação)
    for _, row in df_aguardando.iterrows():
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


def tela_zapi_ranking():
    st.markdown("## 🏆 Ranking de funcionárias")
    st.caption(
        "Calculado em tempo real a partir de CLIENTES + arquivo + INDICACOES + arquivo. "
        "Conta como **cliente** quem enviou pelo menos 1 contato (Total Indicacoes > 0). "
        "Conta como **indicação** cada contato com status VALIDO."
    )

    # ─── Seletor de período (v9.4) ───────────────────────────────────────
    # Filtra DATA BATEU META (clientes) e DATA da indicação (indicações).
    # Filtros independentes — cliente que bateu meta em maio com indicações
    # que entraram em junho conta nas duas métricas separadamente.
    # ───────────────────────────────────────────────────────────────────
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
        "🎯 Personalizado": (None, None),  # vai abrir date_input
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

    # Resolve as datas finais (string ISO ou vazio)
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

    ranking = data.get("ranking", [])
    totais = data.get("totais", {})
    if not ranking:
        st.warning("Nenhum dado no ranking ainda.")
        return

    df = pd.DataFrame(ranking)

    # ─── Filtro por unidade (vem ANTES dos cards pra que eles reflitam o filtro) ───
    unid_filtro = st.radio(
        "Filtrar por unidade:",
        ["Todas", "Mogi", "Suzano"],
        horizontal=True,
        key="rank_unid_filtro",
    )
    df_filtrado = df.copy()
    if unid_filtro != "Todas":
        df_filtrado = df_filtrado[df_filtrado["unidade"].str.lower() == unid_filtro.lower()]

    # ─── Cards de resumo — recalculados a partir do DF FILTRADO ───
    fontes = totais.get("fontes", {})
    if unid_filtro == "Todas":
        # Usa totais do API direto (mais preciso, vem do Apps Script)
        n_func = totais.get("funcionarias_distintas", 0)
        n_cli = totais.get("clientes_com_indicacoes", 0)
        n_ind = totais.get("indicacoes_validas", 0)
    else:
        # Calcula a partir do DF filtrado
        n_func = len(df_filtrado)
        n_cli = int(df_filtrado["clientes_com_indicacoes"].sum())
        n_ind = int(df_filtrado["indicacoes_validas"].sum())

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("👥 Funcionárias distintas", n_func)
    col_b.metric("✅ Clientes que indicaram", n_cli)
    col_c.metric("📨 Indicações válidas", f"{n_ind:,}".replace(",", "."))
    col_d.metric(
        "📚 Fonte de dados",
        f"{fontes.get('clientes_ativos', 0) + fontes.get('clientes_arquivo', 0)} clientes",
        help=(
            f"CLIENTES: {fontes.get('clientes_ativos', 0)} ativos + "
            f"{fontes.get('clientes_arquivo', 0)} arquivados.\n"
            f"INDICACOES: {fontes.get('indicacoes_ativas', 0)} ativas + "
            f"{fontes.get('indicacoes_arquivo', 0)} arquivadas.\n"
            f"(Não filtra por unidade — é a base completa de dados.)"
        ),
    )

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
                    {int(r['clientes_com_indicacoes'])} cliente(s)
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
    df_tabela = df_tabela.rename(columns={
        "funcionaria": "Funcionária",
        "unidade": "Unidade",
        "clientes_com_indicacoes": "Clientes que indicaram",
        "indicacoes_validas": "Indicações válidas",
        "indic_por_cliente": "Indic / cliente",
    })
    st.dataframe(
        df_tabela,
        use_container_width=True,
        hide_index=True,
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "Indicações válidas": st.column_config.NumberColumn(format="%d"),
            "Indic / cliente": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    st.markdown("---")

    # ─── Gráfico de barras horizontal ───
    st.markdown("### 📊 Indicações válidas por funcionária")
    try:
        import plotly.express as px
        df_plot = df.copy()
        df_plot["label"] = df_plot["funcionaria"] + " (" + df_plot["unidade"] + ")"
        # ordenar do menor pro maior pra ficar bonito horizontal (maior em cima)
        df_plot = df_plot.sort_values("indicacoes_validas", ascending=True)
        fig = px.bar(
            df_plot,
            x="indicacoes_validas",
            y="label",
            orientation="h",
            color="unidade",
            color_discrete_map={"Mogi": "#6366f1", "Suzano": "#f59e0b"},
            text="indicacoes_validas",
            labels={"indicacoes_validas": "Indicações válidas", "label": "", "unidade": "Unidade"},
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
def _zapi_get_metricas():
    """Chama o endpoint metricas_funil. Cache 5min (operação pesada)."""
    try:
        url = st.secrets["APPS_SCRIPT_URL_ZAPI"]
        token = st.secrets["APPS_SCRIPT_TOKEN_ZAPI"]
    except Exception:
        return {"_erro": "Configuração ausente: APPS_SCRIPT_URL_ZAPI / APPS_SCRIPT_TOKEN_ZAPI"}

    try:
        resp = requests.get(
            url,
            params={"endpoint": "metricas_funil", "token": token},
            timeout=45, allow_redirects=True,
        )
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

    col_btn, _ = st.columns([1, 5])
    with col_btn:
        if st.button("🔄 Atualizar", key="met_refresh", use_container_width=True):
            _zapi_get_metricas.clear()
            st.rerun()

    with st.spinner("Calculando métricas do funil..."):
        data = _zapi_get_metricas()

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
