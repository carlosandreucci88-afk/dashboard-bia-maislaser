# -*- coding: utf-8 -*-
"""
Card de Cota UrlFetch - 5 robos
v1.0 (07/08/2026)

Consulta 5 endpoints Apps Script (Agenda, Bia, IeG, Pos, MKT) e mostra
consumo diario de UrlFetch. Cotas do Google Workspace Free: 20.000/dia
por script.

Config obrigatoria em .streamlit/secrets.toml:

    [urlfetch]
    agenda_url = "https://script.google.com/macros/s/.../exec"
    agenda_token = "..."
    bia_url = "https://script.google.com/macros/s/.../exec"
    bia_token = "..."
    ieg_url = "https://script.google.com/macros/s/.../exec"
    ieg_token = "..."
    pos_url = "https://script.google.com/macros/s/.../exec"
    pos_token = "..."
    mkt_url = "https://script.google.com/macros/s/.../exec"
    mkt_token = "..."

Estrategia defensiva pra Streamlit 1.35:
    - Sem type hints modernos
    - Sem @st.cache_resource
    - Sem st.container(border=True)
    - Try/except em cada acesso a secrets
    - Concatenacao simples de strings
    - Requests paralelas via ThreadPoolExecutor
"""

import streamlit as st
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================================
# CONFIG DOS ROBOS
# ============================================================================
# 4 primeiros usam ?endpoint=uso, MKT usa ?action=uso
ROBOS_CONFIG = [
    {"nome": "Agenda",     "secret_url": "agenda_url", "secret_token": "agenda_token", "param": "endpoint"},
    {"nome": "Filtro Bia", "secret_url": "bia_url",    "secret_token": "bia_token",    "param": "endpoint"},
    {"nome": "IeG",        "secret_url": "ieg_url",    "secret_token": "ieg_token",    "param": "endpoint"},
    {"nome": "Pos",        "secret_url": "pos_url",    "secret_token": "pos_token",    "param": "endpoint"},
    {"nome": "MKT",        "secret_url": "mkt_url",    "secret_token": "mkt_token",    "param": "action"},
]

LIMITE_DIA_DEFAULT = 20000


# ============================================================================
# HELPERS
# ============================================================================
def _get_secret(chave):
    """Retorna valor do secret ou None se ausente."""
    try:
        return st.secrets["urlfetch"][chave]
    except Exception:
        try:
            return st.secrets[chave]
        except Exception:
            return None


def _fetch_uso_robo(config):
    """
    Chama endpoint 'uso' do robo e retorna dict com resultado.
    Timeout 5s. Retorna dict com erro em caso de falha.
    """
    nome = config["nome"]
    url = _get_secret(config["secret_url"])
    token = _get_secret(config["secret_token"])
    param = config["param"]

    if not url or not token:
        return {
            "nome": nome,
            "ok": False,
            "erro": "url/token nao configurado em secrets.toml",
            "urlfetch_hoje": 0,
            "limite_dia": LIMITE_DIA_DEFAULT,
            "pct": 0,
        }

    try:
        params = {param: "uso", "token": token}
        r = requests.get(url, params=params, timeout=5, allow_redirects=True)
        if r.status_code != 200:
            return {
                "nome": nome,
                "ok": False,
                "erro": "HTTP " + str(r.status_code),
                "urlfetch_hoje": 0,
                "limite_dia": LIMITE_DIA_DEFAULT,
                "pct": 0,
            }
        data = r.json()
        # Tolerante: pode vir dentro de outro campo dependendo do robo
        if isinstance(data, dict) and "urlfetch_hoje" in data:
            payload = data
        elif isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            payload = data["data"]
        else:
            payload = data if isinstance(data, dict) else {}

        return {
            "nome": nome,
            "ok": True,
            "erro": None,
            "urlfetch_hoje": int(payload.get("urlfetch_hoje", 0)),
            "limite_dia": int(payload.get("limite_dia", LIMITE_DIA_DEFAULT)),
            "pct": int(payload.get("pct", 0)),
            "data": payload.get("data", ""),
        }
    except Exception as e:
        return {
            "nome": nome,
            "ok": False,
            "erro": str(e)[:100],
            "urlfetch_hoje": 0,
            "limite_dia": LIMITE_DIA_DEFAULT,
            "pct": 0,
        }


def _fetch_all_paralelo():
    """Chama os 5 endpoints em paralelo com timeout total 6s."""
    resultados = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(_fetch_uso_robo, cfg) for cfg in ROBOS_CONFIG]
        for f in as_completed(futures, timeout=6):
            try:
                resultados.append(f.result())
            except Exception as e:
                resultados.append({"nome": "?", "ok": False, "erro": str(e),
                                    "urlfetch_hoje": 0, "limite_dia": LIMITE_DIA_DEFAULT, "pct": 0})
    # Preservar ordem original
    ordem = {c["nome"]: i for i, c in enumerate(ROBOS_CONFIG)}
    resultados.sort(key=lambda r: ordem.get(r["nome"], 99))
    return resultados


def _cor_semaforo(pct):
    """Verde <60, amarelo 60-85, vermelho >85."""
    if pct >= 85:
        return "vermelho"
    if pct >= 60:
        return "amarelo"
    return "verde"


def _emoji_semaforo(pct):
    if pct >= 85:
        return "🔴"
    if pct >= 60:
        return "🟡"
    return "🟢"


# ============================================================================
# UI
# ============================================================================
def render_cota_urlfetch():
    """Renderiza card completo de cota UrlFetch dos 5 robos."""
    st.markdown("### ⚡ Cota UrlFetch (hoje)")

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        atualizar = st.button("🔄 Atualizar", key="urlfetch_refresh")

    # Sempre busca em cada render pra ter dado fresco (nao usa cache)
    with st.spinner("Consultando os 5 robos..."):
        resultados = _fetch_all_paralelo()

    # Total
    total_hoje = sum(r["urlfetch_hoje"] for r in resultados if r["ok"])
    total_limite = sum(r["limite_dia"] for r in resultados if r["ok"]) or LIMITE_DIA_DEFAULT
    pct_total = int((total_hoje / total_limite) * 100) if total_limite else 0
    total_emoji = _emoji_semaforo(pct_total)

    with col_info:
        st.markdown(
            "**Total sistema:** " + total_emoji + " " +
            str(total_hoje) + " / " + str(total_limite) +
            " (" + str(pct_total) + "%)"
        )

    st.divider()

    # 5 barras (uma por robo)
    for r in resultados:
        nome = r["nome"]
        if not r["ok"]:
            st.error("**" + nome + "** — erro: " + str(r.get("erro") or "desconhecido"))
            continue

        pct = r["pct"]
        hoje = r["urlfetch_hoje"]
        limite = r["limite_dia"]
        emoji = _emoji_semaforo(pct)

        col_nome, col_bar, col_num = st.columns([1, 3, 1])
        with col_nome:
            st.markdown("**" + emoji + " " + nome + "**")
        with col_bar:
            # Progress bar (0.0 a 1.0)
            valor = pct / 100.0
            if valor > 1.0:
                valor = 1.0
            if valor < 0:
                valor = 0.0
            st.progress(valor)
        with col_num:
            st.markdown(str(hoje) + " / " + str(limite))

    # Aviso quando algum >= 85%
    criticos = [r for r in resultados if r["ok"] and r["pct"] >= 85]
    if criticos:
        nomes = ", ".join(c["nome"] for c in criticos)
        st.error(
            "🚨 **Atenção:** " + nomes +
            " passaram de 85% da cota diaria. Risco de estouro."
        )

    st.caption(
        "Cota Google Workspace Free: 20.000 fetches/dia por script. "
        "Reseta 00:00 SP. Verde <60% | Amarelo 60-85% | Vermelho >85%."
    )
