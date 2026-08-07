"""
==============================================================================
ROBÔ MARKETING — Aba "📤 Disparos MKT"
==============================================================================
v1.1 (06/08/2026) — aviso janela 24h no telefone_alerta

Fluxo:
    1. Sub-aba 📤 Nova campanha:
        - Escolhe unidade (Mogi/Suzano)
        - Nome da campanha, template (Meta), telefone alerta (recepção)
        - Upload XLSX/CSV com nome + telefone
        - Valida (normaliza, dedup interno, opt-out, dedup 60d, bloqueio
          cruzado contra Bia/Agenda ativos)
        - Preview: N vão receber / N bloqueados por motivo
        - Botão CRIAR (grava mkt_campanhas + mkt_disparos, status RASCUNHO)
        - Botão CRIAR E DISPARAR (idem + inicia loop de envio)

    2. Sub-aba 📊 Ativas:
        - Lista campanhas RASCUNHO / PENDENTE / RODANDO / PAUSADA
        - Progresso: X de N enviados, % entregue, % lido, % respondido
        - Ações: iniciar, pausar, retomar, cancelar, disparar restante

    3. Sub-aba 📈 Relatório:
        - Todas as campanhas (paginadas)
        - Métricas agregadas: total disparos, taxa entrega, leitura, resposta
        - Ranking de campanhas por % resposta
        - Drill-down: clique numa campanha vê detalhes

    4. Sub-aba 🚫 Opt-outs:
        - Lista de opt-outs (mkt_opt_outs)
        - Adicionar manualmente
        - Remover (com confirmação)

Arquitetura:
    - Streamlit envia DIRETO pra Meta API (padrão do aba_pos_disparar.py)
    - Cron do Apps Script fica como BACKUP automático: se browser fechar
      no meio de um disparo, o cron pega os disparos que sobraram em FILA
      e continua enviando em background
    - Unicidade garantida: mkt_disparos tem UNIQUE (campanha_id, telefone) +
      status transita de FILA -> ENVIADO na hora do envio, então Streamlit
      e Cron nunca disputam o mesmo disparo

Config obrigatória em st.secrets (.streamlit/secrets.toml):
    SUPABASE_URL         = "https://pmorwdbmzbeaakutxhdk.supabase.co"
    SUPABASE_KEY         = "..."     (service_role JWT)
    TOKEN_META           = "..."     (mesmo dos outros robôs, do Business Maislaser)
    META_PHONE_ID_MKT    = "..."     (do número novo — preencher quando Meta aprovar)

Constantes:
    TEMPLATE_LANG_DEFAULT = pt_BR
    META_API_VERSION      = v25.0
    RITMO_SEGUNDOS_DEFAULT = 1
==============================================================================
"""

import streamlit as st
import pandas as pd
import requests
import re
import io
import time
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))

# ============================================================================
# CONSTANTES
# ============================================================================

META_API_VERSION       = "v25.0"
TEMPLATE_LANG_DEFAULT  = "pt_BR"
RITMO_SEGUNDOS_DEFAULT = 1
DEDUP_DIAS             = 60
BATCH_INSERT_SIZE      = 500
UNIDADES               = ["mogi", "suzano"]

# Estados da campanha
STATUS_CAMP = {
    "RASCUNHO":   "🟨 Rascunho",
    "PENDENTE":   "🟦 Pendente",
    "RODANDO":    "🟢 Rodando",
    "PAUSADA":    "🟡 Pausada",
    "CANCELADA":  "🔴 Cancelada",
    "FINALIZADA": "✅ Finalizada",
    "ARQUIVADA":  "⚪ Arquivada",
}

# Estados do disparo
STATUS_DISP_ATIVOS = ["FILA", "ENVIADO", "ENTREGUE", "LIDO", "RESPONDIDO"]
STATUS_DISP_SKIPS  = ["SKIP_DEDUP", "SKIP_OPTOUT", "SKIP_ATIVO", "SKIP_INVALIDO"]
STATUS_DISP_ERROS  = ["ERRO"]


# ============================================================================
# CONEXÃO SUPABASE
# ============================================================================
@st.cache_resource
def _get_sb() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# ============================================================================
# CONFIG META
# ============================================================================

def _meta_config_ok() -> tuple[bool, str]:
    """
    Retorna (ok, mensagem_erro).
    Verifica se TOKEN_META e META_PHONE_ID_MKT estão configurados.
    """
    if "TOKEN_META" not in st.secrets or not st.secrets["TOKEN_META"]:
        return False, "TOKEN_META não configurado em .streamlit/secrets.toml"
    if "META_PHONE_ID_MKT" not in st.secrets or not st.secrets["META_PHONE_ID_MKT"]:
        return False, ("META_PHONE_ID_MKT não configurado. Preencha em secrets.toml "
                       "com o Phone ID do número novo (quando Meta aprovar).")
    return True, ""


# ============================================================================
# NORMALIZAÇÃO DE TELEFONE (mesma lógica do Apps Script)
# ============================================================================

def normalizar_telefone(input_str) -> str | None:
    """
    Retorna telefone em formato 5511XXXXXXXXX (13 dígitos com 55) ou None.
    Aceita: 5511XXXXXXXXX, 11XXXXXXXXX, (11) 9XXXX-XXXX, 9XXXXXXXX (sem DDD → assume 11).
    """
    if input_str is None:
        return None
    l = re.sub(r"\D", "", str(input_str))
    if not l:
        return None

    # Já com 55
    if l.startswith("55") and len(l) in (12, 13):
        return l

    # Sem 55, com DDD (11 dígitos celular ou 10 fixo antigo)
    if len(l) == 11 and re.match(r"^[1-9][1-9]9", l):
        return "55" + l
    if len(l) == 10 and re.match(r"^[1-9][1-9]", l):
        return "55" + l

    # Sem DDD, 9 dígitos celular (começa com 9)
    if len(l) == 9 and l.startswith("9"):
        return "5511" + l

    # Sem DDD, 8 dígitos fixo antigo
    if len(l) == 8:
        return "5511" + l

    return None


def telefone_valido(tel: str) -> bool:
    if not tel:
        return False
    l = re.sub(r"\D", "", str(tel))
    if not l.startswith("55"):
        return False
    if len(l) not in (12, 13):
        return False
    ddd = int(l[2:4])
    if not (11 <= ddd <= 99):
        return False
    # Se 13 dígitos, próximo após DDD tem que ser 9 (celular)
    if len(l) == 13 and l[4] != "9":
        return False
    return True


def formatar_telefone_visual(tel: str) -> str:
    """5511958167833 → (11) 95816-7833"""
    if not tel or len(tel) < 12:
        return tel
    if tel.startswith("55"):
        tel = tel[2:]
    if len(tel) == 11:
        return f"({tel[:2]}) {tel[2:7]}-{tel[7:]}"
    if len(tel) == 10:
        return f"({tel[:2]}) {tel[2:6]}-{tel[6:]}"
    return tel


# ============================================================================
# UPLOAD + PARSER
# ============================================================================

def parsear_upload(arquivo, coluna_nome: str | None = None, coluna_tel: str | None = None) -> tuple[list, list]:
    """
    Retorna (contatos, avisos).
    contatos = [{"nome": str, "telefone": str_original}, ...]
    """
    avisos = []
    contatos = []

    try:
        nome_arquivo = arquivo.name.lower()
        if nome_arquivo.endswith(".csv"):
            df = pd.read_csv(arquivo, dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(arquivo, dtype=str, keep_default_na=False)
    except Exception as e:
        avisos.append(f"❌ Erro ao ler arquivo: {e}")
        return [], avisos

    if df.empty:
        avisos.append("❌ Arquivo vazio.")
        return [], avisos

    # Se colunas não informadas, tenta detectar automaticamente
    if not coluna_nome or not coluna_tel:
        # Heurística simples: procura por palavras-chave
        for col in df.columns:
            cl = str(col).lower().strip()
            if not coluna_nome and any(k in cl for k in ["nome", "cliente", "name"]):
                coluna_nome = col
            if not coluna_tel and any(k in cl for k in ["telefone", "celular", "whatsapp", "fone", "phone", "tel"]):
                coluna_tel = col

    if not coluna_nome or coluna_nome not in df.columns:
        avisos.append(f"❌ Coluna de nome não encontrada. Colunas disponíveis: {list(df.columns)}")
        return [], avisos
    if not coluna_tel or coluna_tel not in df.columns:
        avisos.append(f"❌ Coluna de telefone não encontrada. Colunas disponíveis: {list(df.columns)}")
        return [], avisos

    for _, row in df.iterrows():
        nome = str(row[coluna_nome]).strip()
        tel = str(row[coluna_tel]).strip()
        if not tel:
            continue
        contatos.append({"nome": nome or "Cliente", "telefone": tel})

    if not contatos:
        avisos.append("❌ Nenhum contato válido encontrado.")

    return contatos, avisos


# ============================================================================
# VALIDAÇÃO E CLASSIFICAÇÃO DE CONTATOS
# ============================================================================

def analisar_contatos(sb: Client, contatos: list, unidade: str) -> dict:
    """
    Retorna dict com totais + lista de disparos prontos pra INSERT.
    Espelha _analisarContatos do Apps Script.
    """
    # 1. Normaliza + valida formato + dedup interno
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

    telefones_validos = [x["telefone"] for x in validos]

    # 2. Opt-outs (mkt_opt_outs) - batches de 100
    opt_outs = _query_in_batches(
        sb, "mkt_opt_outs", "telefone", telefones_validos, ["telefone"]
    )

    # 3. Dedup 60 dias (só conta ENTREGUE/LIDO/RESPONDIDO)
    dedup60 = _consultar_dedup_60d(sb, telefones_validos)

    # 4. Bloqueio Bia (clientes ativos na mesma unidade)
    bloq_bia = _consultar_bloqueio_bia(sb, telefones_validos, unidade)

    # 5. Bloqueio Agenda (agenda_contexto ativos na mesma unidade)
    bloq_agenda = _consultar_bloqueio_agenda(sb, telefones_validos, unidade)

    # 6. Classifica
    disparos = []
    cValidos = cDedup = cOptout = cAtivo = 0

    for v in validos:
        tel = v["telefone"]
        if tel in opt_outs:
            status, motivo = "SKIP_OPTOUT", "opt_out_global"
            cOptout += 1
        elif tel in dedup60:
            status, motivo = "SKIP_DEDUP", "recebeu_60d"
            cDedup += 1
        elif tel in bloq_bia:
            status, motivo = "SKIP_ATIVO", "ativo_bia"
            cAtivo += 1
        elif tel in bloq_agenda:
            status, motivo = "SKIP_ATIVO", "ativo_agenda"
            cAtivo += 1
        else:
            status, motivo = "FILA", None
            cValidos += 1
        disparos.append({
            "telefone": tel,
            "nome": v["nome"],
            "status": status,
            "motivo_skip": motivo,
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


def _query_in_batches(sb: Client, tabela: str, coluna_filtro: str,
                      telefones: list, colunas_select: list) -> set:
    """
    Retorna set de telefones encontrados na tabela.
    Faz em batches de 100 pra não estourar URL.
    """
    achados = set()
    if not telefones:
        return achados
    for i in range(0, len(telefones), 100):
        batch = telefones[i:i+100]
        try:
            r = sb.table(tabela).select(",".join(colunas_select)).in_(coluna_filtro, batch).execute()
            for row in r.data or []:
                achados.add(row.get(coluna_filtro))
        except Exception as e:
            st.warning(f"⚠️ Erro consultando {tabela}: {e}")
    return achados


def _consultar_dedup_60d(sb: Client, telefones: list) -> set:
    """
    Retorna set de telefones que já receberam MKT ENTREGUE/LIDO/RESPONDIDO
    nos últimos 60 dias.
    """
    achados = set()
    if not telefones:
        return achados
    limite = (datetime.now(TZ_SP) - timedelta(days=DEDUP_DIAS)).isoformat()
    for i in range(0, len(telefones), 100):
        batch = telefones[i:i+100]
        try:
            r = (sb.table("mkt_disparos")
                   .select("telefone")
                   .in_("status", ["ENTREGUE", "LIDO", "RESPONDIDO"])
                   .gte("disparado_em", limite)
                   .in_("telefone", batch)
                   .execute())
            for row in r.data or []:
                achados.add(row["telefone"])
        except Exception as e:
            st.warning(f"⚠️ Erro consultando dedup 60d: {e}")
    return achados


def _consultar_bloqueio_bia(sb: Client, telefones: list, unidade: str) -> set:
    """
    Bloqueia se cliente está ativa no Bia (mesma unidade + status não-terminal).
    """
    achados = set()
    if not telefones:
        return achados
    STATUS_TERMINAIS = ["FINALIZADO", "ENCERRADO", "INVALIDADO_COBRADO",
                        "_COBRADOSEMRESPOSTA", "INVALIDADO_AVISADO"]
    for i in range(0, len(telefones), 100):
        batch = telefones[i:i+100]
        try:
            r = (sb.table("clientes")
                   .select("telefone")
                   .is_("arquivada_em", "null")
                   .eq("unidade", unidade)
                   .not_.in_("status_de_aonde_parou", STATUS_TERMINAIS)
                   .in_("telefone", batch)
                   .execute())
            for row in r.data or []:
                achados.add(row["telefone"])
        except Exception as e:
            st.warning(f"⚠️ Erro consultando bloqueio Bia: {e}")
    return achados


def _consultar_bloqueio_agenda(sb: Client, telefones: list, unidade: str) -> set:
    """
    Bloqueia se cliente está ativa na Agenda (mesma unidade, arquivado_em NULL).
    Agenda usa unidade capitalizada: "Mogi das Cruzes" / "Suzano".
    """
    achados = set()
    if not telefones:
        return achados
    unidade_agenda = "Mogi das Cruzes" if unidade == "mogi" else "Suzano"
    for i in range(0, len(telefones), 100):
        batch = telefones[i:i+100]
        try:
            r = (sb.table("agenda_contexto")
                   .select("telefone")
                   .is_("arquivado_em", "null")
                   .eq("unidade", unidade_agenda)
                   .in_("telefone", batch)
                   .execute())
            for row in r.data or []:
                achados.add(row["telefone"])
        except Exception as e:
            st.warning(f"⚠️ Erro consultando bloqueio Agenda: {e}")
    return achados


# ============================================================================
# CRIAR CAMPANHA (INSERT campanha + batch INSERT disparos)
# ============================================================================

def criar_campanha(sb: Client, dados_campanha: dict, disparos_prep: list) -> tuple[int | None, str]:
    """
    INSERT mkt_campanhas + batch INSERT mkt_disparos.
    Retorna (campanha_id, mensagem_erro_ou_"").
    """
    try:
        # 1. INSERT campanha
        r = sb.table("mkt_campanhas").insert(dados_campanha).execute()
        if not r.data:
            return None, "Falha ao criar campanha (retornou vazio)"
        campanha_id = r.data[0]["id"]
    except Exception as e:
        return None, f"Erro INSERT campanha: {e}"

    if not disparos_prep:
        return campanha_id, ""

    # 2. Prepara disparos
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

    # 3. Batch INSERT (chunks de 500)
    try:
        for i in range(0, len(rows), BATCH_INSERT_SIZE):
            chunk = rows[i:i+BATCH_INSERT_SIZE]
            sb.table("mkt_disparos").insert(chunk).execute()
    except Exception as e:
        return campanha_id, f"⚠️ Campanha criada (id={campanha_id}), mas erro nos disparos: {e}"

    return campanha_id, ""


# ============================================================================
# ENVIO VIA META API (direto, padrão pos_disparar)
# ============================================================================

def enviar_template_meta(telefone_e164: str, template_nome: str, template_lang: str,
                         nome_cliente: str) -> tuple[bool, str, str]:
    """
    Envia template com placeholder {{1}} = nome do cliente.
    Retorna (ok, wamid_ou_erro, erro_texto).
    """
    ok, msg = _meta_config_ok()
    if not ok:
        return False, "", msg

    phone_id = st.secrets["META_PHONE_ID_MKT"]
    token = st.secrets["TOKEN_META"]
    url = f"https://graph.facebook.com/{META_API_VERSION}/{phone_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefone_e164,
        "type": "template",
        "template": {
            "name": template_nome,
            "language": {"code": template_lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(nome_cliente or "Cliente")[:60]}
                    ]
                }
            ]
        }
    }

    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=15
        )
        if r.status_code >= 300:
            return False, "", f"HTTP {r.status_code}: {r.text[:300]}"
        data = r.json()
        wamid = data.get("messages", [{}])[0].get("id", "")
        if not wamid:
            return False, "", f"sem_wamid: {r.text[:200]}"
        return True, wamid, ""
    except Exception as e:
        return False, "", f"exception: {e}"


# ============================================================================
# LOOP DE DISPARO (padrão aba_pos_disparar)
# ============================================================================

def disparar_campanha_loop(sb: Client, campanha_id: int, ritmo_seg: int = 1):
    """
    Pega todos os disparos em FILA da campanha e envia 1 por 1.
    Streaming ao vivo com progress bar. Padrão aba_pos_disparar.
    """
    # 1. Pega campanha
    r = sb.table("mkt_campanhas").select("*").eq("id", campanha_id).execute()
    if not r.data:
        st.error(f"❌ Campanha {campanha_id} não encontrada.")
        return
    camp = r.data[0]

    # 2. Marca RODANDO se ainda não
    if camp["status"] not in ("RODANDO", "PENDENTE"):
        sb.table("mkt_campanhas").update({
            "status": "RODANDO",
            "iniciado_em": camp.get("iniciado_em") or datetime.now(TZ_SP).isoformat()
        }).eq("id", campanha_id).execute()

    # 3. Pega FILA
    r = (sb.table("mkt_disparos")
           .select("id,telefone,nome")
           .eq("campanha_id", campanha_id)
           .eq("status", "FILA")
           .order("fila_em")
           .execute())
    fila = r.data or []

    if not fila:
        st.info("✅ Nada em FILA. Verificando se pode finalizar...")
        _talvez_finalizar(sb, campanha_id)
        return

    total = len(fila)
    st.markdown(f"### 🚀 Disparando **{total}** mensagens (ritmo {ritmo_seg}s)")

    progress_bar = st.progress(0.0)
    status_txt = st.empty()
    log_area = st.empty()

    sucessos = 0
    erros = 0
    logs = []

    template_nome = camp["template_nome"]
    template_lang = camp.get("template_lang") or TEMPLATE_LANG_DEFAULT

    for idx, disp in enumerate(fila, start=1):
        # Verifica se campanha foi pausada/cancelada meio caminho
        r2 = sb.table("mkt_campanhas").select("status").eq("id", campanha_id).execute()
        status_atual = (r2.data[0]["status"] if r2.data else "RODANDO")
        if status_atual in ("PAUSADA", "CANCELADA"):
            st.warning(f"⏸️ Campanha em {status_atual}. Loop interrompido em {idx-1}/{total}.")
            break

        # Envia
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
            logs.append(f"✅ {formatar_telefone_visual(disp['telefone'])} — {disp['nome']}")
        else:
            sb.table("mkt_disparos").update({
                "status": "ERRO",
                "erro_msg": erro_txt[:500],
                "tentativas_envio": 1,
            }).eq("id", disp["id"]).execute()
            erros += 1
            logs.append(f"❌ {formatar_telefone_visual(disp['telefone'])} — {erro_txt[:80]}")

        # Progress bar + log (últimos 8)
        progress_bar.progress(idx / total)
        status_txt.markdown(
            f"**{idx}/{total}** — ✅ {sucessos} enviados / ❌ {erros} erros"
        )
        log_area.markdown("\n".join(f"- {l}" for l in logs[-8:]))

        # Sleep antes do próximo (menos no último)
        if idx < len(fila):
            time.sleep(max(1, ritmo_seg))

    # 4. Finaliza se acabou tudo
    _talvez_finalizar(sb, campanha_id)

    st.success(f"🎉 Disparo concluído: {sucessos} enviados, {erros} erros de {total}")
    st.balloons()


def _talvez_finalizar(sb: Client, campanha_id: int):
    """Se não sobrou nada em FILA, marca FINALIZADA."""
    r = (sb.table("mkt_disparos")
           .select("id", count="exact")
           .eq("campanha_id", campanha_id)
           .eq("status", "FILA")
           .limit(1)
           .execute())
    if not r.data:
        sb.table("mkt_campanhas").update({
            "status": "FINALIZADA",
            "finalizado_em": datetime.now(TZ_SP).isoformat()
        }).eq("id", campanha_id).execute()


# ============================================================================
# QUERIES DE LISTAGEM
# ============================================================================
@st.cache_data(ttl=15, show_spinner=False)
def _listar_campanhas(status_in: list | None = None, limit: int = 50) -> pd.DataFrame:
    sb = _get_sb()
    q = sb.table("mkt_campanhas").select("*").order("criado_em", desc=True).limit(limit)
    if status_in:
        q = q.in_("status", status_in)
    r = q.execute()
    if not r.data:
        return pd.DataFrame()
    return pd.DataFrame(r.data)


@st.cache_data(ttl=15, show_spinner=False)
def _metricas_campanha(campanha_id: int) -> dict:
    sb = _get_sb()
    r = sb.table("v_mkt_metricas_campanha").select("*").eq("campanha_id", campanha_id).execute()
    if not r.data:
        return {}
    return r.data[0]


@st.cache_data(ttl=15, show_spinner=False)
def _listar_opt_outs(limit: int = 200) -> pd.DataFrame:
    sb = _get_sb()
    r = sb.table("mkt_opt_outs").select("*").order("criado_em", desc=True).limit(limit).execute()
    if not r.data:
        return pd.DataFrame()
    return pd.DataFrame(r.data)


# ============================================================================
# UI: SUB-ABA "NOVA CAMPANHA"
# ============================================================================

def _sub_aba_nova_campanha():
    st.markdown("### 📤 Nova Campanha")

    # Verifica config Meta primeiro
    meta_ok, meta_msg = _meta_config_ok()
    if not meta_ok:
        st.error(f"⚠️ Meta ainda não configurado: {meta_msg}")
        st.info("Você pode criar a campanha em **RASCUNHO** — depois preenche o secrets e dispara.")

    col1, col2 = st.columns(2)

    with col1:
        nome_campanha = st.text_input(
            "Nome da campanha",
            placeholder="Promo agosto Suzano",
            help="Nome interno pra você identificar depois no relatório."
        )
        unidade = st.selectbox(
            "Unidade",
            options=UNIDADES,
            format_func=lambda u: "Mogi" if u == "mogi" else "Suzano",
            help="Todos os disparos herdam essa unidade. Bloqueio cruzado usa isso."
        )
        template_nome = st.text_input(
            "Nome do template Meta",
            placeholder="mkt_isca_ativos_v1",
            help="Nome exato do template aprovado no Meta Business Manager."
        )

    with col2:
        telefone_alerta = st.text_input(
            "Telefone recepção (alerta)",
            placeholder="(11) 99999-9999",
            help="Recebe wa.me com link quando cliente responder."
        )
        # v1.1: Aviso sobre janela 24h (Meta Cloud API)
        _numero_mkt = st.secrets.get("NUMERO_MKT_DISPLAY", "XXXXXXXXXXX")
        st.caption(
            f"⚠️ **Antes de disparar:** abra a janela de 24h mandando uma mensagem "
            f"do WhatsApp da recepção pro número MKT **{_numero_mkt}**. "
            f"Sem isso, os alertas de resposta não chegam."
        )
        template_lang = st.text_input("Idioma do template", value="pt_BR")
        ritmo = st.number_input(
            "Ritmo (segundos entre disparos)",
            min_value=1, max_value=60, value=RITMO_SEGUNDOS_DEFAULT,
            help="1 = padrão, respeitando tier Meta"
        )

    st.markdown("---")
    st.markdown("### 📎 Upload de contatos")

    arquivo = st.file_uploader(
        "Excel (XLSX) ou CSV com colunas Nome e Telefone",
        type=["xlsx", "xls", "csv"],
        help="Colunas detectadas automaticamente por nome (procura por 'nome' e 'telefone')."
    )

    if not arquivo:
        st.info("👆 Faça upload da planilha pra continuar.")
        return

    contatos, avisos = parsear_upload(arquivo)
    for a in avisos:
        if a.startswith("❌"):
            st.error(a)
        else:
            st.warning(a)

    if not contatos:
        return

    st.success(f"✅ {len(contatos)} linhas lidas da planilha.")

    # Preview 5 primeiras
    with st.expander("👀 Preview (5 primeiras linhas)", expanded=False):
        st.dataframe(pd.DataFrame(contatos[:5]))

    st.markdown("---")

    # Botão VALIDAR — dispara análise contra Supabase
    if st.button("🔍 Validar contatos (dedup + opt-out + bloqueio cruzado)",
                 use_container_width=True, type="secondary"):
        _validar_e_guardar_no_state(contatos, unidade)
        st.rerun()

    # Se já validou, mostra resultado + botão criar
    if st.session_state.get("mkt_analise") and st.session_state.get("mkt_analise_unidade") == unidade:
        analise = st.session_state["mkt_analise"]

        st.markdown("### 📊 Resultado da validação")
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1: st.metric("Total upload", analise["total_upload"])
        with c2: st.metric("✅ Vão receber", analise["total_validos"])
        with c3: st.metric("⏭️ Skip dedup 60d", analise["total_skip_dedup"])
        with c4: st.metric("🚫 Opt-outs", analise["total_skip_optout"])
        with c5: st.metric("🔗 Ativos outros robôs", analise["total_skip_ativo"])

        if analise["total_skip_invalido"] > 0:
            st.warning(f"⚠️ {analise['total_skip_invalido']} telefone(s) inválido(s) descartado(s) (não entram nem como skip).")

        if analise["total_validos"] == 0:
            st.error("❌ Nenhum contato válido pra disparar. Ajuste a lista ou revise os filtros.")
            return

        st.markdown("---")

        # Botões de criação
        col_a, col_b = st.columns(2)

        with col_a:
            criar_only = st.button(
                "💾 Criar campanha (RASCUNHO — não dispara agora)",
                use_container_width=True
            )

        with col_b:
            criar_e_disparar = st.button(
                f"🚀 Criar e disparar {analise['total_validos']} agora",
                use_container_width=True, type="primary",
                disabled=not meta_ok
            )

        if criar_only or criar_e_disparar:
            _executar_criacao(
                nome_campanha, template_nome, template_lang, telefone_alerta,
                unidade, ritmo, analise, iniciar_apos=criar_e_disparar
            )


def _validar_e_guardar_no_state(contatos: list, unidade: str):
    sb = _get_sb()
    with st.spinner("🔍 Validando contatos (isso pode levar ~10-30s)..."):
        analise = analisar_contatos(sb, contatos, unidade)
    st.session_state["mkt_analise"] = analise
    st.session_state["mkt_analise_unidade"] = unidade


def _executar_criacao(nome, template_nome, template_lang, telefone_alerta,
                       unidade, ritmo, analise, iniciar_apos: bool):
    # Validações
    if not nome:
        st.error("❌ Nome da campanha obrigatório.")
        return
    if not template_nome:
        st.error("❌ Nome do template Meta obrigatório.")
        return
    tel_alerta_norm = normalizar_telefone(telefone_alerta)
    if not tel_alerta_norm or not telefone_valido(tel_alerta_norm):
        st.error("❌ Telefone de alerta inválido.")
        return

    sb = _get_sb()

    dados_campanha = {
        "nome":                nome,
        "template_nome":       template_nome,
        "template_lang":       template_lang or TEMPLATE_LANG_DEFAULT,
        "telefone_alerta":     tel_alerta_norm,
        "unidade":             unidade,
        "ritmo_segundos":      int(ritmo),
        "status":              "RODANDO" if iniciar_apos else "RASCUNHO",
        "total_upload":        analise["total_upload"],
        "total_validos":       analise["total_validos"],
        "total_skip_dedup":    analise["total_skip_dedup"],
        "total_skip_optout":   analise["total_skip_optout"],
        "total_skip_ativo":    analise["total_skip_ativo"],
        "total_skip_invalido": analise["total_skip_invalido"],
        "total_leads":         analise["total_validos"],  # compat com view v_mkt_metricas_campanha
        "criado_por":          "streamlit",
        "iniciado_em":         datetime.now(TZ_SP).isoformat() if iniciar_apos else None,
    }

    with st.spinner("💾 Criando campanha e gravando disparos..."):
        campanha_id, erro = criar_campanha(sb, dados_campanha, analise["disparos"])

    if erro:
        st.error(f"❌ {erro}")
        if not campanha_id:
            return

    st.success(f"✅ Campanha criada! ID = **{campanha_id}**")

    # v1.1: Lembrete critico sobre janela 24h
    _numero_mkt = st.secrets.get("NUMERO_MKT_DISPLAY", "XXXXXXXXXXX")
    st.info(
        f"🔔 **Lembrete:** a recepção precisa ter mandado uma mensagem "
        f"pro número MKT (**{_numero_mkt}**) nas últimas 24h pra receber "
        f"os alertas de resposta. Caso contrário, veja as respostas "
        f"pelo dashboard na aba **📊 Ativas**."
    )

    # Limpa session state pra próxima campanha
    for k in ("mkt_analise", "mkt_analise_unidade"):
        if k in st.session_state:
            del st.session_state[k]
    st.cache_data.clear()

    if iniciar_apos:
        st.markdown("---")
        disparar_campanha_loop(sb, campanha_id, int(ritmo))


# ============================================================================
# UI: SUB-ABA "ATIVAS"
# ============================================================================

def _sub_aba_ativas():
    st.markdown("### 📊 Campanhas ativas")

    df = _listar_campanhas(
        status_in=["RASCUNHO", "PENDENTE", "RODANDO", "PAUSADA"],
        limit=50
    )

    if df.empty:
        st.info("Nenhuma campanha ativa no momento.")
        return

    sb = _get_sb()

    for _, row in df.iterrows():
        camp_id = row["id"]
        with st.container(border=True):
            col_info, col_metricas, col_acoes = st.columns([2, 2, 1])

            with col_info:
                st.markdown(f"**#{camp_id} — {row['nome']}**")
                st.caption(
                    f"{STATUS_CAMP.get(row['status'], row['status'])} · "
                    f"{'Mogi' if row['unidade']=='mogi' else 'Suzano'} · "
                    f"template `{row['template_nome']}`"
                )
                st.caption(f"Criada: {_fmt_data(row.get('criado_em'))}")

            with col_metricas:
                m = _metricas_campanha(camp_id)
                if m:
                    tot = m.get("total_leads") or 0
                    disp = m.get("disparados") or 0
                    ent = m.get("entregues") or 0
                    lid = m.get("lidos") or 0
                    resp = m.get("respondidos") or 0
                    pct_disp = (disp/tot*100) if tot else 0
                    st.markdown(
                        f"**{disp}/{tot}** disparados ({pct_disp:.0f}%) · "
                        f"{ent} entregues · {lid} lidos · **{resp} respostas**"
                    )
                    st.progress(pct_disp/100 if pct_disp <= 100 else 1.0)

            with col_acoes:
                st.markdown(" ")  # espaço vertical
                _renderizar_acoes(sb, row)

        st.markdown("")


def _renderizar_acoes(sb: Client, row):
    camp_id = row["id"]
    status = row["status"]

    if status == "RASCUNHO":
        if st.button("🚀 Iniciar", key=f"start_{camp_id}", use_container_width=True):
            _acao_iniciar(sb, camp_id, int(row.get("ritmo_segundos") or 1))
            st.rerun()

    elif status in ("PENDENTE", "RODANDO"):
        if st.button("⏸️ Pausar", key=f"pause_{camp_id}", use_container_width=True):
            sb.table("mkt_campanhas").update({"status": "PAUSADA"}).eq("id", camp_id).execute()
            st.cache_data.clear()
            st.rerun()

    elif status == "PAUSADA":
        if st.button("▶️ Retomar", key=f"resume_{camp_id}", use_container_width=True):
            _acao_iniciar(sb, camp_id, int(row.get("ritmo_segundos") or 1))
            st.rerun()

    if status in ("RASCUNHO", "PENDENTE", "RODANDO", "PAUSADA"):
        if st.button("🔴 Cancelar", key=f"cancel_{camp_id}", use_container_width=True):
            sb.table("mkt_campanhas").update({
                "status": "CANCELADA",
                "finalizado_em": datetime.now(TZ_SP).isoformat()
            }).eq("id", camp_id).execute()
            st.cache_data.clear()
            st.rerun()


def _acao_iniciar(sb: Client, camp_id: int, ritmo: int):
    sb.table("mkt_campanhas").update({
        "status": "RODANDO",
        "iniciado_em": datetime.now(TZ_SP).isoformat()
    }).eq("id", camp_id).execute()
    st.cache_data.clear()
    # Executa o loop de disparo agora
    disparar_campanha_loop(sb, camp_id, ritmo)


# ============================================================================
# UI: SUB-ABA "RELATÓRIO"
# ============================================================================

def _sub_aba_relatorio():
    st.markdown("### 📈 Relatório de campanhas")

    df = _listar_campanhas(limit=100)
    if df.empty:
        st.info("Nenhuma campanha registrada.")
        return

    sb = _get_sb()

    # Enriquece com métricas
    linhas = []
    for _, row in df.iterrows():
        m = _metricas_campanha(row["id"])
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
            "ID":           row["id"],
            "Nome":         row["nome"],
            "Unidade":      "Mogi" if row["unidade"]=="mogi" else "Suzano",
            "Status":       STATUS_CAMP.get(row["status"], row["status"]),
            "Template":     row["template_nome"],
            "Alvo":         tot,
            "Disparados":   disp,
            "Entregues":    ent,
            "Lidos":        lid,
            "Respondidos":  resp,
            "Positivas":    pos,
            "Negativas":    neg,
            "% Entrega":    f"{pct_ent:.0f}%",
            "% Leitura":    f"{pct_lid:.0f}%",
            "% Resposta":   f"{pct_resp:.0f}%",
            "Criada":       _fmt_data(row.get("criado_em")),
        })

    df_view = pd.DataFrame(linhas)
    st.dataframe(df_view, use_container_width=True, height=500, hide_index=True)

    # Ranking por % resposta (só campanhas com pelo menos 20 disparos)
    st.markdown("---")
    st.markdown("### 🏆 Top 10 campanhas por % de resposta")
    df_rank = df_view.copy()
    df_rank["% Resposta num"] = df_rank["% Resposta"].str.replace("%","").astype(float)
    df_rank = df_rank[df_rank["Disparados"] >= 20].sort_values("% Resposta num", ascending=False).head(10)
    if df_rank.empty:
        st.info("Ainda não há campanhas com pelo menos 20 disparos pra ranquear.")
    else:
        st.dataframe(
            df_rank[["Nome", "Unidade", "Disparados", "Respondidos", "% Resposta"]],
            use_container_width=True, hide_index=True
        )


# ============================================================================
# UI: SUB-ABA "OPT-OUTS"
# ============================================================================

def _sub_aba_opt_outs():
    st.markdown("### 🚫 Opt-outs MKT")
    st.caption("Lista de números que pediram pra não receber campanhas. Bloqueio global.")

    sb = _get_sb()

    # Adicionar manualmente
    with st.expander("➕ Adicionar opt-out manual"):
        colf1, colf2, colf3 = st.columns([2, 2, 1])
        with colf1:
            novo_tel = st.text_input("Telefone", placeholder="(11) 99999-9999", key="opt_add_tel")
        with colf2:
            motivo = st.text_input("Motivo", value="manual", key="opt_add_motivo")
        with colf3:
            st.markdown(" ")
            if st.button("Adicionar", use_container_width=True):
                tel_norm = normalizar_telefone(novo_tel)
                if not tel_norm or not telefone_valido(tel_norm):
                    st.error("❌ Telefone inválido")
                else:
                    try:
                        sb.table("mkt_opt_outs").upsert(
                            {"telefone": tel_norm, "motivo": motivo or "manual"}
                        ).execute()
                        st.success(f"✅ {formatar_telefone_visual(tel_norm)} adicionado")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

    st.markdown("---")

    df = _listar_opt_outs(limit=500)
    if df.empty:
        st.info("Nenhum opt-out registrado.")
        return

    # Formata telefone visual
    df["Telefone"] = df["telefone"].apply(formatar_telefone_visual)
    df["Criado em"] = df["criado_em"].apply(_fmt_data)
    df["Motivo"] = df.get("motivo", "")
    df["Origem"] = df.get("campanha_origem", "").fillna("-")

    st.dataframe(
        df[["Telefone", "Motivo", "Origem", "Criado em"]],
        use_container_width=True, height=400, hide_index=True
    )

    st.caption(f"Total: **{len(df)}** opt-out(s).")

    # Remover
    with st.expander("🗑️ Remover opt-out (com cuidado — vai voltar a receber)"):
        tel_remover = st.text_input("Telefone pra remover", placeholder="(11) 99999-9999", key="opt_rm_tel")
        if st.button("Remover"):
            tel_norm = normalizar_telefone(tel_remover)
            if not tel_norm:
                st.error("❌ Telefone inválido")
            else:
                try:
                    r = sb.table("mkt_opt_outs").delete().eq("telefone", tel_norm).execute()
                    st.success(f"✅ Removido: {formatar_telefone_visual(tel_norm)}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {e}")


# ============================================================================
# HELPER: formatar timestamp
# ============================================================================

def _fmt_data(ts) -> str:
    if not ts:
        return "-"
    try:
        # ISO com timezone
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(TZ_SP).strftime("%d/%m %H:%M")
    except Exception:
        return str(ts)[:16]


# ============================================================================
# ENTRY POINT
# ============================================================================

def render_aba_marketing():
    """Chamada pelo dashboard_maislaser.py quando robô ativo é 'mkt'."""

    # Aviso configuração Meta se ausente
    meta_ok, meta_msg = _meta_config_ok()
    if not meta_ok:
        st.warning(f"⚠️ **{meta_msg}** — Você pode criar/testar campanhas em RASCUNHO até resolver.")

    (tab_nova, tab_ativas, tab_relatorio, tab_optouts) = st.tabs([
        "📤 Nova campanha",
        "📊 Ativas",
        "📈 Relatório",
        "🚫 Opt-outs",
    ])

    with tab_nova:
        _sub_aba_nova_campanha()

    with tab_ativas:
        _sub_aba_ativas()

    with tab_relatorio:
        _sub_aba_relatorio()

    with tab_optouts:
        _sub_aba_opt_outs()
