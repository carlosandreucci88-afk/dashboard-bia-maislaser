# -*- coding: utf-8 -*-
"""
Robo Marketing - Aba Disparos MKT
v1.5 (07/08/2026)

v1.5: UX FIX — troca st.tabs por st.radio horizontal com estado persistente
      via session_state (st.tabs no Streamlit 1.35 nao guarda tab selecionada
      entre reruns e voltava sempre pra 'Nova campanha'). Adiciona tambem
      botao '🔄 Atualizar' local em cada card da aba Ativas pra ver progresso
      evoluindo sem sair da aba.
v1.4: DISPARO ASSINCRONO — backend cron dispararProximoLote processa a fila;
      UI nao bloqueia mais e pode ser fechada sem perda.

Streamlit envia direto pra Meta Cloud API (padrao aba_pos_disparar).
Cron do Apps Script fica como backup automatico.

Config obrigatoria em .streamlit/secrets.toml:
    SUPABASE_URL
    SUPABASE_KEY
    TOKEN_META
    META_PHONE_ID_MKT
    NUMERO_MKT_DISPLAY (opcional)
"""

import re
import time
import io
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple

import streamlit as st
import pandas as pd
import requests
from supabase import create_client, Client


# ============================================================================
# CONSTANTES
# ============================================================================
TZ_SP = timezone(timedelta(hours=-3))

META_API_VERSION       = "v25.0"
TEMPLATE_LANG_DEFAULT  = "pt_BR"
RITMO_SEGUNDOS_DEFAULT = 1
DEDUP_DIAS             = 60
BATCH_INSERT_SIZE      = 500
UNIDADES               = ["mogi", "suzano"]

# v1.3 (07/08/2026): Templates aprovados pela Meta (nome, descricao)
TEMPLATES_DISPONIVEIS = [
    ("confirmacaosessao_80",   "confirmacaosessao_80 (sessao gratis + areas)"),
    ("confirmacaosessao_comb", "confirmacaosessao_comb (combos com 80%)"),
]

STATUS_CAMP = {
    "RASCUNHO":   "Rascunho",
    "PENDENTE":   "Pendente",
    "RODANDO":    "Rodando",
    "PAUSADA":    "Pausada",
    "CANCELADA":  "Cancelada",
    "FINALIZADA": "Finalizada",
    "ARQUIVADA":  "Arquivada",
}


# ============================================================================
# SUPABASE
# ============================================================================
def _get_sb():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# ============================================================================
# CONFIG META
# ============================================================================
def _meta_config_ok():
    try:
        token = st.secrets.get("TOKEN_META", "") if hasattr(st.secrets, "get") else st.secrets["TOKEN_META"]
        if not token:
            return False, "TOKEN_META nao configurado em secrets.toml"
    except Exception:
        return False, "TOKEN_META nao configurado em secrets.toml"

    try:
        phone = st.secrets.get("META_PHONE_ID_MKT", "") if hasattr(st.secrets, "get") else st.secrets["META_PHONE_ID_MKT"]
        if not phone:
            return False, "META_PHONE_ID_MKT nao configurado (preencha quando Meta aprovar o numero)"
    except Exception:
        return False, "META_PHONE_ID_MKT nao configurado (preencha quando Meta aprovar o numero)"

    return True, ""


def _numero_mkt_display():
    try:
        return st.secrets["NUMERO_MKT_DISPLAY"]
    except Exception:
        return "XXXXXXXXXXX"


# ============================================================================
# NORMALIZACAO DE TELEFONE
# ============================================================================
def normalizar_telefone(input_str):
    if input_str is None:
        return None
    l = re.sub(r"\D", "", str(input_str))
    if not l:
        return None

    if l.startswith("55") and len(l) in (12, 13):
        return l

    if len(l) == 11 and re.match(r"^[1-9][1-9]9", l):
        return "55" + l
    if len(l) == 10 and re.match(r"^[1-9][1-9]", l):
        return "55" + l

    if len(l) == 9 and l.startswith("9"):
        return "5511" + l
    if len(l) == 8:
        return "5511" + l

    return None


def telefone_valido(tel):
    if not tel:
        return False
    l = re.sub(r"\D", "", str(tel))
    if not l.startswith("55"):
        return False
    if len(l) not in (12, 13):
        return False
    try:
        ddd = int(l[2:4])
    except Exception:
        return False
    if not (11 <= ddd <= 99):
        return False
    if len(l) == 13 and l[4] != "9":
        return False
    return True


def formatar_telefone_visual(tel):
    if not tel or len(tel) < 12:
        return tel
    if tel.startswith("55"):
        tel = tel[2:]
    if len(tel) == 11:
        return "(" + tel[:2] + ") " + tel[2:7] + "-" + tel[7:]
    if len(tel) == 10:
        return "(" + tel[:2] + ") " + tel[2:6] + "-" + tel[6:]
    return tel


# ============================================================================
# UPLOAD PARSER
# ============================================================================
def parsear_upload(arquivo):
    avisos = []
    contatos = []

    try:
        nome_arquivo = arquivo.name.lower()
        if nome_arquivo.endswith(".csv"):
            df = pd.read_csv(arquivo, dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(arquivo, dtype=str, keep_default_na=False)
    except Exception as e:
        avisos.append("Erro ao ler arquivo: " + str(e))
        return [], avisos

    if df.empty:
        avisos.append("Arquivo vazio.")
        return [], avisos

    coluna_nome = None
    coluna_tel = None
    for col in df.columns:
        cl = str(col).lower().strip()
        if not coluna_nome and any(k in cl for k in ["nome", "cliente", "name"]):
            coluna_nome = col
        if not coluna_tel and any(k in cl for k in ["telefone", "celular", "whatsapp", "fone", "phone", "tel"]):
            coluna_tel = col

    if not coluna_nome:
        avisos.append("Coluna de nome nao encontrada. Colunas: " + str(list(df.columns)))
        return [], avisos
    if not coluna_tel:
        avisos.append("Coluna de telefone nao encontrada. Colunas: " + str(list(df.columns)))
        return [], avisos

    for _, row in df.iterrows():
        nome = str(row[coluna_nome]).strip()
        tel = str(row[coluna_tel]).strip()
        if not tel:
            continue
        contatos.append({"nome": nome or "Cliente", "telefone": tel})

    if not contatos:
        avisos.append("Nenhum contato valido encontrado.")

    return contatos, avisos


# ============================================================================
# CONSULTAS DE VALIDACAO
# ============================================================================
def _chunks(lst, size):
    out = []
    for i in range(0, len(lst), size):
        out.append(lst[i:i+size])
    return out


def _consultar_opt_outs(sb, telefones):
    achados = set()
    if not telefones:
        return achados
    for batch in _chunks(telefones, 100):
        try:
            r = sb.table("mkt_opt_outs").select("telefone").in_("telefone", batch).execute()
            for row in (r.data or []):
                achados.add(row["telefone"])
        except Exception as e:
            st.warning("Erro consultando opt-outs: " + str(e))
    return achados


def _consultar_dedup_60d(sb, telefones):
    achados = set()
    if not telefones:
        return achados
    limite = (datetime.now(TZ_SP) - timedelta(days=DEDUP_DIAS)).isoformat()
    for batch in _chunks(telefones, 100):
        try:
            r = (sb.table("mkt_disparos")
                   .select("telefone")
                   .in_("status", ["ENTREGUE", "LIDO", "RESPONDIDO"])
                   .gte("disparado_em", limite)
                   .in_("telefone", batch)
                   .execute())
            for row in (r.data or []):
                achados.add(row["telefone"])
        except Exception as e:
            st.warning("Erro consultando dedup 60d: " + str(e))
    return achados


def _consultar_bloqueio_bia(sb, telefones, unidade):
    achados = set()
    if not telefones:
        return achados
    terminais = ["FINALIZADO", "ENCERRADO", "INVALIDADO_COBRADO",
                 "_COBRADOSEMRESPOSTA", "INVALIDADO_AVISADO"]
    for batch in _chunks(telefones, 100):
        try:
            r = (sb.table("clientes")
                   .select("telefone")
                   .is_("arquivada_em", "null")
                   .eq("unidade", unidade)
                   .not_.in_("status_de_aonde_parou", terminais)
                   .in_("telefone", batch)
                   .execute())
            for row in (r.data or []):
                achados.add(row["telefone"])
        except Exception as e:
            st.warning("Erro consultando bloqueio Bia: " + str(e))
    return achados


def _consultar_bloqueio_agenda(sb, telefones, unidade):
    achados = set()
    if not telefones:
        return achados
    unidade_agenda = "Mogi das Cruzes" if unidade == "mogi" else "Suzano"
    for batch in _chunks(telefones, 100):
        try:
            r = (sb.table("agenda_contexto")
                   .select("telefone")
                   .is_("arquivado_em", "null")
                   .eq("unidade", unidade_agenda)
                   .in_("telefone", batch)
                   .execute())
            for row in (r.data or []):
                achados.add(row["telefone"])
        except Exception as e:
            st.warning("Erro consultando bloqueio Agenda: " + str(e))
    return achados


def analisar_contatos(sb, contatos, unidade):
    validos = []
    total_invalido = 0
    seen = set()

    for c in contatos:
        nome = str(c.get("nome") or "").strip() or "Cliente"
        tel = normalizar_telefone(c.get("telefone"))
        if not tel or not telefone_valido(tel):
            total_invalido += 1
            continue
        if tel in seen:
            continue
        seen.add(tel)
        validos.append({"telefone": tel, "nome": nome})

    telefones = [x["telefone"] for x in validos]

    opt_outs   = _consultar_opt_outs(sb, telefones)
    dedup60    = _consultar_dedup_60d(sb, telefones)
    bloq_bia   = _consultar_bloqueio_bia(sb, telefones, unidade)
    bloq_ag    = _consultar_bloqueio_agenda(sb, telefones, unidade)

    disparos = []
    cValidos = cDedup = cOptout = cAtivo = 0

    for v in validos:
        tel = v["telefone"]
        if tel in opt_outs:
            status = "SKIP_OPTOUT"; motivo = "opt_out_global"; cOptout += 1
        elif tel in dedup60:
            status = "SKIP_DEDUP"; motivo = "recebeu_60d"; cDedup += 1
        elif tel in bloq_bia:
            status = "SKIP_ATIVO"; motivo = "ativo_bia"; cAtivo += 1
        elif tel in bloq_ag:
            status = "SKIP_ATIVO"; motivo = "ativo_agenda"; cAtivo += 1
        else:
            status = "FILA"; motivo = None; cValidos += 1
        disparos.append({
            "telefone": tel, "nome": v["nome"],
            "status": status, "motivo_skip": motivo,
        })

    return {
        "total_upload":        len(contatos),
        "total_validos":       cValidos,
        "total_skip_dedup":    cDedup,
        "total_skip_optout":   cOptout,
        "total_skip_ativo":    cAtivo,
        "total_skip_invalido": total_invalido,
        "disparos":            disparos,
    }


# ============================================================================
# CRIAR CAMPANHA
# ============================================================================
def criar_campanha(sb, dados_campanha, disparos_prep):
    try:
        r = sb.table("mkt_campanhas").insert(dados_campanha).execute()
        if not r.data:
            return None, "Falha ao criar campanha (retorno vazio)"
        campanha_id = r.data[0]["id"]
    except Exception as e:
        return None, "Erro INSERT campanha: " + str(e)

    if not disparos_prep:
        return campanha_id, ""

    unidade = dados_campanha["unidade"]
    rows = []
    for d in disparos_prep:
        rows.append({
            "campanha_id":  campanha_id,
            "telefone":     d["telefone"],
            "nome":         d["nome"],
            "unidade":      unidade,
            "status":       d["status"],
            "motivo_skip":  d.get("motivo_skip"),
        })

    try:
        for i in range(0, len(rows), BATCH_INSERT_SIZE):
            chunk = rows[i:i+BATCH_INSERT_SIZE]
            sb.table("mkt_disparos").insert(chunk).execute()
    except Exception as e:
        return campanha_id, "Campanha criada (id=" + str(campanha_id) + "), erro nos disparos: " + str(e)

    return campanha_id, ""


# ============================================================================
# META API (envio direto)
# ============================================================================
def enviar_template_meta(telefone_e164, template_nome, template_lang, nome_cliente):
    ok, msg = _meta_config_ok()
    if not ok:
        return False, "", msg

    phone_id = st.secrets["META_PHONE_ID_MKT"]
    token = st.secrets["TOKEN_META"]
    url = "https://graph.facebook.com/" + META_API_VERSION + "/" + phone_id + "/messages"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefone_e164,
        "type": "template",
        "template": {
            "name": template_nome,
            "language": {"code": template_lang},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(nome_cliente or "Cliente")[:60]}]
            }]
        }
    }

    try:
        r = requests.post(
            url,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            json=payload, timeout=15
        )
        if r.status_code >= 300:
            return False, "", "HTTP " + str(r.status_code) + ": " + r.text[:300]
        data = r.json()
        wamid = ""
        try:
            wamid = data["messages"][0]["id"]
        except Exception:
            pass
        if not wamid:
            return False, "", "sem_wamid: " + r.text[:200]
        return True, wamid, ""
    except Exception as e:
        return False, "", "exception: " + str(e)


# ============================================================================
# LOOP DE DISPARO
# ============================================================================
def disparar_campanha_loop(sb, campanha_id, ritmo_seg=1):
    r = sb.table("mkt_campanhas").select("*").eq("id", campanha_id).execute()
    if not r.data:
        st.error("Campanha " + str(campanha_id) + " nao encontrada.")
        return
    camp = r.data[0]

    if camp["status"] not in ("RODANDO", "PENDENTE"):
        sb.table("mkt_campanhas").update({
            "status": "RODANDO",
            "iniciado_em": camp.get("iniciado_em") or datetime.now(TZ_SP).isoformat()
        }).eq("id", campanha_id).execute()

    r = (sb.table("mkt_disparos")
           .select("id,telefone,nome")
           .eq("campanha_id", campanha_id)
           .eq("status", "FILA")
           .order("fila_em")
           .execute())
    fila = r.data or []

    if not fila:
        st.info("Nada em FILA.")
        _talvez_finalizar(sb, campanha_id)
        return

    total = len(fila)
    st.markdown("### Disparando **" + str(total) + "** mensagens (ritmo " + str(ritmo_seg) + "s)")

    pbar = st.progress(0.0)
    status_txt = st.empty()
    log_area = st.empty()

    sucessos = 0
    erros = 0
    logs = []

    template_nome = camp["template_nome"]
    template_lang = camp.get("template_lang") or TEMPLATE_LANG_DEFAULT

    for idx, disp in enumerate(fila, start=1):
        r2 = sb.table("mkt_campanhas").select("status").eq("id", campanha_id).execute()
        st_atual = (r2.data[0]["status"] if r2.data else "RODANDO")
        if st_atual in ("PAUSADA", "CANCELADA"):
            st.warning("Campanha em " + st_atual + ". Loop interrompido em " + str(idx-1) + "/" + str(total))
            break

        ok, wamid_ou_erro, erro_txt = enviar_template_meta(
            disp["telefone"], template_nome, template_lang, disp["nome"]
        )

        agora = datetime.now(TZ_SP).isoformat()
        if ok:
            sb.table("mkt_disparos").update({
                "status": "ENVIADO",
                "disparado_em": agora,
                "wamid": wamid_ou_erro,
                "tentativas_envio": 1,
            }).eq("id", disp["id"]).execute()
            sucessos += 1
            logs.append("OK  " + formatar_telefone_visual(disp["telefone"]) + " - " + disp["nome"])
        else:
            sb.table("mkt_disparos").update({
                "status": "ERRO",
                "erro_msg": erro_txt[:500],
                "tentativas_envio": 1,
            }).eq("id", disp["id"]).execute()
            erros += 1
            logs.append("ERR " + formatar_telefone_visual(disp["telefone"]) + " - " + erro_txt[:80])

        pbar.progress(idx / total)
        status_txt.markdown("**" + str(idx) + "/" + str(total) + "** - OK " + str(sucessos) + " / ERR " + str(erros))
        log_area.markdown("\n".join(["- " + l for l in logs[-8:]]))

        if idx < len(fila):
            time.sleep(max(1, ritmo_seg))

    _talvez_finalizar(sb, campanha_id)
    st.success("Disparo concluido: " + str(sucessos) + " enviados, " + str(erros) + " erros de " + str(total))


def _talvez_finalizar(sb, campanha_id):
    try:
        r = (sb.table("mkt_disparos")
               .select("id")
               .eq("campanha_id", campanha_id)
               .eq("status", "FILA")
               .limit(1)
               .execute())
        if not r.data:
            sb.table("mkt_campanhas").update({
                "status": "FINALIZADA",
                "finalizado_em": datetime.now(TZ_SP).isoformat()
            }).eq("id", campanha_id).execute()
    except Exception:
        pass


# ============================================================================
# HELPERS DE LISTAGEM (sem cache_resource pra evitar problemas de import)
# ============================================================================
def _listar_campanhas(sb, status_in=None, limit=50):
    q = sb.table("mkt_campanhas").select("*").order("criado_em", desc=True).limit(limit)
    if status_in:
        q = q.in_("status", status_in)
    r = q.execute()
    if not r.data:
        return pd.DataFrame()
    return pd.DataFrame(r.data)


def _metricas_campanha(sb, campanha_id):
    r = sb.table("v_mkt_metricas_campanha").select("*").eq("campanha_id", campanha_id).execute()
    if not r.data:
        return {}
    return r.data[0]


def _fmt_data(ts):
    if not ts:
        return "-"
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(TZ_SP).strftime("%d/%m %H:%M")
    except Exception:
        return str(ts)[:16]


# ============================================================================
# UI: NOVA CAMPANHA
# ============================================================================
def _sub_aba_nova_campanha(sb, meta_ok, meta_msg):
    st.markdown("### Nova Campanha")

    if not meta_ok:
        st.error("Meta ainda nao configurado: " + meta_msg)
        st.info("Voce pode criar campanha em RASCUNHO agora e disparar depois.")

    col1, col2 = st.columns(2)

    with col1:
        nome_campanha = st.text_input("Nome da campanha", placeholder="Promo agosto Suzano")
        unidade = st.selectbox("Unidade", options=UNIDADES,
                                format_func=lambda u: "Mogi" if u == "mogi" else "Suzano")
        # v1.3: template agora e selectbox (2 templates aprovados)
        template_idx = st.selectbox(
            "Template Meta",
            options=list(range(len(TEMPLATES_DISPONIVEIS))),
            format_func=lambda i: TEMPLATES_DISPONIVEIS[i][1]
        )
        template_nome = TEMPLATES_DISPONIVEIS[template_idx][0]

    with col2:
        # v1.3: Nome da atendente ACIMA do telefone recepcao (vira {{2}} no template)
        nome_atendente = st.text_input(
            "Nome da atendente",
            placeholder="Rebeca",
            help="Aparece no {{2}} do template, no follow-up e nos alertas pra recepcao."
        )
        telefone_alerta = st.text_input("Telefone recepcao (alerta)", placeholder="(11) 99999-9999")
        numero_mkt = _numero_mkt_display()
        st.caption(
            "Antes de disparar: abra a janela de 24h mandando uma mensagem "
            "do WhatsApp da recepcao pro numero MKT **" + numero_mkt + "**. "
            "Sem isso, os alertas de resposta nao chegam."
        )
        template_lang = st.text_input("Idioma do template", value="pt_BR")
        ritmo = st.number_input("Ritmo (segundos entre disparos)",
                                 min_value=1, max_value=60, value=RITMO_SEGUNDOS_DEFAULT)

    st.markdown("---")
    st.markdown("### Upload de contatos")

    arquivo = st.file_uploader("Excel (XLSX) ou CSV com colunas Nome e Telefone",
                                type=["xlsx", "xls", "csv"])

    if not arquivo:
        st.info("Faca upload da planilha pra continuar.")
        return

    contatos, avisos = parsear_upload(arquivo)
    for a in avisos:
        st.warning(a)

    if not contatos:
        return

    st.success(str(len(contatos)) + " linhas lidas da planilha.")

    with st.expander("Preview (5 primeiras)", expanded=False):
        st.dataframe(pd.DataFrame(contatos[:5]))

    st.markdown("---")

    if st.button("Validar contatos (dedup + opt-out + bloqueio cruzado)",
                 use_container_width=True):
        with st.spinner("Validando..."):
            analise = analisar_contatos(sb, contatos, unidade)
        st.session_state["mkt_analise"] = analise
        st.session_state["mkt_analise_unidade"] = unidade
        st.rerun()

    if (st.session_state.get("mkt_analise")
            and st.session_state.get("mkt_analise_unidade") == unidade):
        analise = st.session_state["mkt_analise"]

        st.markdown("### Resultado da validacao")
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1: st.metric("Total upload", analise["total_upload"])
        with c2: st.metric("Vao receber", analise["total_validos"])
        with c3: st.metric("Skip dedup 60d", analise["total_skip_dedup"])
        with c4: st.metric("Opt-outs", analise["total_skip_optout"])
        with c5: st.metric("Ativos outros robos", analise["total_skip_ativo"])

        if analise["total_skip_invalido"] > 0:
            st.warning(str(analise["total_skip_invalido"]) + " telefone(s) invalido(s) descartado(s).")

        if analise["total_validos"] == 0:
            st.error("Nenhum contato valido pra disparar.")
            return

        st.markdown("---")

        cola, colb = st.columns(2)
        with cola:
            criar_only = st.button("Criar campanha (RASCUNHO)",
                                    use_container_width=True)
        with colb:
            criar_e_disparar = st.button(
                "Criar e disparar " + str(analise["total_validos"]) + " agora",
                use_container_width=True, type="primary", disabled=not meta_ok)

        if criar_only or criar_e_disparar:
            _executar_criacao(sb, nome_campanha, template_nome, template_lang,
                              telefone_alerta, unidade, ritmo, analise,
                              criar_e_disparar, nome_atendente)


def _executar_criacao(sb, nome, template_nome, template_lang, telefone_alerta,
                       unidade, ritmo, analise, iniciar_apos, nome_atendente=None):
    if not nome:
        st.error("Nome da campanha obrigatorio.")
        return
    if not template_nome:
        st.error("Nome do template obrigatorio.")
        return
    # v1.3: nome_atendente obrigatorio
    if not nome_atendente or not str(nome_atendente).strip():
        st.error("Nome da atendente obrigatorio.")
        return
    tel_alerta_norm = normalizar_telefone(telefone_alerta)
    if not tel_alerta_norm or not telefone_valido(tel_alerta_norm):
        st.error("Telefone de alerta invalido.")
        return

    dados = {
        "nome":                nome,
        "template_nome":       template_nome,
        "template_lang":       template_lang or TEMPLATE_LANG_DEFAULT,
        "telefone_alerta":     tel_alerta_norm,
        "nome_atendente":      str(nome_atendente).strip()[:60],
        "unidade":             unidade,
        "ritmo_segundos":      int(ritmo),
        "status":              "RODANDO" if iniciar_apos else "RASCUNHO",
        "total_upload":        analise["total_upload"],
        "total_validos":       analise["total_validos"],
        "total_skip_dedup":    analise["total_skip_dedup"],
        "total_skip_optout":   analise["total_skip_optout"],
        "total_skip_ativo":    analise["total_skip_ativo"],
        "total_skip_invalido": analise["total_skip_invalido"],
        "total_leads":         analise["total_validos"],
        "criado_por":          "streamlit",
        "iniciado_em":         datetime.now(TZ_SP).isoformat() if iniciar_apos else None,
    }

    with st.spinner("Criando campanha..."):
        campanha_id, erro = criar_campanha(sb, dados, analise["disparos"])

    if erro:
        st.error(erro)
        if not campanha_id:
            return

    st.success("Campanha criada! ID = " + str(campanha_id))

    numero_mkt = _numero_mkt_display()
    st.info(
        "Lembrete: a recepcao precisa ter mandado uma mensagem pro numero MKT (**"
        + numero_mkt + "**) nas ultimas 24h pra receber alertas. Caso contrario, veja as respostas "
        "pelo dashboard na aba Ativas."
    )

    for k in ("mkt_analise", "mkt_analise_unidade"):
        if k in st.session_state:
            del st.session_state[k]

    if iniciar_apos:
        # v1.4 (07/08/2026): DISPARO ASSINCRONO
        # Backend (cron dispararProximoLote do Apps Script) processa a fila.
        # Streamlit nao bloqueia mais - usuario pode fechar aba sem perda.
        st.success(
            "🚀 Campanha em execucao! Backend esta enviando as mensagens em segundo plano. "
            "Voce pode fechar esta aba sem problemas. "
            "Acompanhe o progresso na aba **Ativas**."
        )
        st.info(
            "Ritmo: ~60 msg/min (limite do cron). Para " + str(analise["total_validos"]) +
            " contatos, previsao de conclusao: ~" +
            str(max(1, round(analise["total_validos"] / 60))) + " minuto(s)."
        )


# ============================================================================
# UI: ATIVAS
# ============================================================================
def _sub_aba_ativas(sb):
    st.markdown("### Campanhas ativas")

    df = _listar_campanhas(sb, status_in=["RASCUNHO", "PENDENTE", "RODANDO", "PAUSADA"], limit=50)

    if df.empty:
        st.info("Nenhuma campanha ativa no momento.")
        return

    for _, row in df.iterrows():
        camp_id = row["id"]
        with st.container():
            col_info, col_metricas, col_acoes = st.columns([2, 2, 1])

            with col_info:
                st.markdown("**#" + str(camp_id) + " - " + str(row["nome"]) + "**")
                st.caption(
                    STATUS_CAMP.get(row["status"], row["status"]) + " | "
                    + ("Mogi" if row["unidade"] == "mogi" else "Suzano")
                    + " | template " + str(row["template_nome"])
                )
                st.caption("Criada: " + _fmt_data(row.get("criado_em")))

            with col_metricas:
                m = _metricas_campanha(sb, camp_id)
                if m:
                    tot = m.get("total_leads") or 0
                    disp = m.get("disparados") or 0
                    ent = m.get("entregues") or 0
                    lid = m.get("lidos") or 0
                    resp = m.get("respondidos") or 0
                    pct = (disp/tot*100) if tot else 0
                    st.markdown(
                        "**" + str(disp) + "/" + str(tot) + "** disparados (" + str(int(pct)) + "%) | "
                        + str(ent) + " entregues | " + str(lid) + " lidos | **" + str(resp) + " respostas**"
                    )
                    st.progress(pct/100 if pct <= 100 else 1.0)

            with col_acoes:
                _renderizar_acoes(sb, row)

            st.markdown("---")


def _renderizar_acoes(sb, row):
    camp_id = row["id"]
    status = row["status"]

    if status == "RASCUNHO":
        if st.button("Iniciar", key="start_" + str(camp_id), use_container_width=True):
            _acao_iniciar(sb, camp_id, int(row.get("ritmo_segundos") or 1))
            st.rerun()

    elif status in ("PENDENTE", "RODANDO"):
        if st.button("Pausar", key="pause_" + str(camp_id), use_container_width=True):
            sb.table("mkt_campanhas").update({"status": "PAUSADA"}).eq("id", camp_id).execute()
            st.rerun()

    elif status == "PAUSADA":
        if st.button("Retomar", key="resume_" + str(camp_id), use_container_width=True):
            _acao_iniciar(sb, camp_id, int(row.get("ritmo_segundos") or 1))
            st.rerun()

    if status in ("RASCUNHO", "PENDENTE", "RODANDO", "PAUSADA"):
        if st.button("Cancelar", key="cancel_" + str(camp_id), use_container_width=True):
            sb.table("mkt_campanhas").update({
                "status": "CANCELADA",
                "finalizado_em": datetime.now(TZ_SP).isoformat()
            }).eq("id", camp_id).execute()
            st.rerun()

    # v1.5: botao local pra refresh sem sair da aba Ativas
    # (session_state[mkt_aba_ativa] preserva a aba no rerun)
    if st.button("🔄 Atualizar", key="refresh_" + str(camp_id), use_container_width=True):
        st.rerun()


def _acao_iniciar(sb, camp_id, ritmo):
    # v1.4 (07/08/2026): assincrono - so muda status, backend cron processa a fila
    sb.table("mkt_campanhas").update({
        "status": "RODANDO",
        "iniciado_em": datetime.now(TZ_SP).isoformat()
    }).eq("id", camp_id).execute()
    st.success(
        "🚀 Campanha #" + str(camp_id) + " iniciada! Backend esta enviando em segundo plano. "
        "Voce pode fechar esta aba."
    )


# ============================================================================
# UI: RELATORIO
# ============================================================================
def _sub_aba_relatorio(sb):
    st.markdown("### Relatorio de campanhas")

    df = _listar_campanhas(sb, limit=100)
    if df.empty:
        st.info("Nenhuma campanha registrada.")
        return

    linhas = []
    for _, row in df.iterrows():
        m = _metricas_campanha(sb, row["id"])
        tot = m.get("total_leads") or 0
        disp = m.get("disparados") or 0
        ent = m.get("entregues") or 0
        lid = m.get("lidos") or 0
        resp = m.get("respondidos") or 0
        pos = m.get("positivas") or 0
        neg = m.get("negativas") or 0
        pct_ent = (ent/disp*100) if disp else 0
        pct_lid = (lid/disp*100) if disp else 0
        pct_resp = (resp/disp*100) if disp else 0
        linhas.append({
            "ID":          row["id"],
            "Nome":        row["nome"],
            "Unidade":     "Mogi" if row["unidade"] == "mogi" else "Suzano",
            "Status":      STATUS_CAMP.get(row["status"], row["status"]),
            "Template":    row["template_nome"],
            "Alvo":        tot,
            "Disparados":  disp,
            "Entregues":   ent,
            "Lidos":       lid,
            "Respondidos": resp,
            "Positivas":   pos,
            "Negativas":   neg,
            "% Entrega":   str(int(pct_ent)) + "%",
            "% Leitura":   str(int(pct_lid)) + "%",
            "% Resposta":  str(int(pct_resp)) + "%",
            "Criada":      _fmt_data(row.get("criado_em")),
        })

    df_view = pd.DataFrame(linhas)
    st.dataframe(df_view, use_container_width=True, height=500, hide_index=True)


# ============================================================================
# UI: OPT-OUTS
# ============================================================================
def _sub_aba_opt_outs(sb):
    st.markdown("### Opt-outs MKT")
    st.caption("Numeros que pediram pra nao receber campanhas. Bloqueio global.")

    with st.expander("Adicionar opt-out manual"):
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            novo_tel = st.text_input("Telefone", placeholder="(11) 99999-9999", key="opt_add_tel")
        with c2:
            motivo = st.text_input("Motivo", value="manual", key="opt_add_motivo")
        with c3:
            if st.button("Adicionar", use_container_width=True):
                tel_norm = normalizar_telefone(novo_tel)
                if not tel_norm or not telefone_valido(tel_norm):
                    st.error("Telefone invalido")
                else:
                    try:
                        sb.table("mkt_opt_outs").upsert(
                            {"telefone": tel_norm, "motivo": motivo or "manual"}
                        ).execute()
                        st.success("Adicionado")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    st.markdown("---")

    try:
        r = sb.table("mkt_opt_outs").select("*").order("criado_em", desc=True).limit(500).execute()
        df = pd.DataFrame(r.data or [])
    except Exception as e:
        st.error("Erro: " + str(e))
        return

    if df.empty:
        st.info("Nenhum opt-out registrado.")
        return

    df["Telefone"] = df["telefone"].apply(formatar_telefone_visual)
    df["Criado em"] = df["criado_em"].apply(_fmt_data)
    df["Motivo"] = df.get("motivo", "")
    df["Origem"] = df.get("campanha_origem", pd.Series([""]*len(df))).fillna("-")

    st.dataframe(df[["Telefone", "Motivo", "Origem", "Criado em"]],
                  use_container_width=True, height=400, hide_index=True)

    st.caption("Total: " + str(len(df)))


# ============================================================================
# ENTRY POINT
# ============================================================================
def render_aba_marketing():
    sb = _get_sb()
    meta_ok, meta_msg = _meta_config_ok()

    if not meta_ok:
        st.warning(meta_msg + " - Voce pode criar campanhas em RASCUNHO.")

    # v1.5: st.radio horizontal com key persistente em session_state
    # substitui st.tabs (1.35 nao mantem tab selecionada entre reruns).
    aba = st.radio(
        label="Aba do robô MKT",
        options=["Nova campanha", "Ativas", "Relatorio", "Opt-outs"],
        horizontal=True,
        label_visibility="collapsed",
        key="mkt_aba_ativa",
    )

    st.markdown("---")

    if aba == "Nova campanha":
        _sub_aba_nova_campanha(sb, meta_ok, meta_msg)
    elif aba == "Ativas":
        _sub_aba_ativas(sb)
    elif aba == "Relatorio":
        _sub_aba_relatorio(sb)
    elif aba == "Opt-outs":
        _sub_aba_opt_outs(sb)
