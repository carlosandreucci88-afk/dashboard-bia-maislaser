"""
==============================================================================
ROBÔ PÓS-ATENDIMENTO — Aba "Disparar pós-atendimento"
==============================================================================
v1.0 (04/07/2026)

Fluxo:
    1. Toggle unidade (Mogi/Suzano) — igual disparador Agenda
    2. Upload XLSX (padrão UNO: Data, Cliente, Telefone, Serviço, Sala,
       Situação, Realizado por)
    3. Parseamento: agrupa por telefone, limpa nomes/áreas
    4. Preview: tabela + amostra da mensagem final
    5. Verificações pré-disparo:
       - Kill switch pos_habilitado (Supabase)
       - Horário comercial (pos_janela_hora_inicio..fim)
       - Modo manutenção geral
    6. Botão "Disparar":
       - INSERT em pos_atendimento_clientes (1 linha por telefone único)
       - Loop enviando template maislaser_posatendimento_v1 via Meta API
       - Atualiza status: template_enviado ou falha_envio
       - Grava wamid
       - Cria registro em pos_atendimento_disparos_historico

Envio direto via Meta API (não via Apps Script) — mesmo padrão do disparador
Agenda.

Config obrigatória em st.secrets:
    SUPABASE_URL, SUPABASE_KEY, TOKEN_META

Constantes fixas (número do robô + template aprovado):
    PHONE_ID_POS = 1196987226830895
    TEMPLATE_NOME = maislaser_posatendimento_v1
    TEMPLATE_LANG = pt_BR
==============================================================================
"""

import streamlit as st
import pandas as pd
import requests
import re
import io
import json
import time
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

TZ_SP = timezone(timedelta(hours=-3))

# ============================================================================
# CONSTANTES
# ============================================================================
PHONE_ID_POS = "1196987226830895"
TEMPLATE_NOME = "maislaser_posatendimento_v2"
TEMPLATE_LANG = "pt_BR"
META_API_VERSION = "v22.0"

# ============================================================================
# CONEXÃO SUPABASE
# ============================================================================
@st.cache_resource
def _get_sb() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=30, show_spinner=False)
def _get_config_pos() -> dict:
    """Lê configuracoes pos_* + modo_manutencao."""
    try:
        sb = _get_sb()
        r = sb.table("configuracoes").select(
            "pos_habilitado,pos_janela_hora_inicio,pos_janela_hora_fim,"
            "pos_coord_mogi_telefone,pos_coord_suzano_telefone,"
            "modo_manutencao"
        ).eq("id", 1).execute()
        if r.data:
            return r.data[0]
    except Exception as e:
        st.error(f"⚠️ Erro ao ler configurações: {e}")
    return {}


# ============================================================================
# LIMPEZA DE DADOS
# ============================================================================

def limpar_servico(s: str) -> str:
    """
    'F - Depilação de Axilas cortesia' → 'Axilas'
    'M - Depilação de Perianal (área P) Cortesia' → 'Perianal'
    """
    if not s:
        return ""
    s = str(s)
    s = re.sub(r'^[FM]\s*-\s*', '', s)
    s = re.sub(r'Depilação\s+de\s+', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*cortesia\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\(área\s+[MGP]\)\s*', '', s, flags=re.IGNORECASE)
    return s.strip()


def primeiro_nome_cliente(nome: str) -> str:
    """
    Extrai primeiro nome, removendo:
    - Underscore inicial: _Alessandra → Alessandra
    - Parênteses com lixo: 'Nome (11 97162-6503) (pagolivre)' → Nome
    - Parênteses com CPF: 'Nome (Cpf 251.637...)' → Nome
    """
    if not nome:
        return "cliente"
    nome = str(nome).strip()
    nome = re.sub(r'^_+', '', nome)
    nome = re.sub(r'\s*\([^)]*\)\s*', ' ', nome)
    nome = re.sub(r'\s+', ' ', nome).strip()
    partes = nome.split()
    if not partes:
        return "cliente"
    primeiro = partes[0]
    return primeiro.title()


def primeiro_ultimo_profissional(nome: str) -> str:
    """
    'Clícia Maria Medeiros Costa' → 'Clícia Costa'
    'Priscila Gonçalves da Paz' → 'Priscila Paz'
    """
    if not nome:
        return "profissional"
    partes = str(nome).strip().split()
    if len(partes) == 0:
        return "profissional"
    if len(partes) == 1:
        return partes[0].title()
    return f"{partes[0].title()} {partes[-1].title()}"


def formatar_areas(servicos: list, max_areas: int = 3) -> str:
    """
    ['F - Depilação de Axilas cortesia', 'F - Depilação de Virilha biquíni cortesia', ...]
    → 'Axilas, Virilha biquíni, ... e mais X áreas'
    """
    limpos = [limpar_servico(s) for s in servicos if s]
    vistos = set()
    unicos = []
    for l in limpos:
        if l and l not in vistos:
            vistos.add(l)
            unicos.append(l)
    if not unicos:
        return "áreas realizadas"
    if len(unicos) <= max_areas:
        return ", ".join(unicos)
    return ", ".join(unicos[:max_areas]) + f" e mais {len(unicos)-max_areas} áreas"


def formatar_data(dt) -> str:
    """datetime → '02/07'"""
    try:
        return dt.strftime("%d/%m")
    except Exception:
        return str(dt)[:5]


def formatar_hora(dt) -> str:
    """datetime → '14h15'"""
    try:
        return dt.strftime("%Hh%M")
    except Exception:
        return str(dt)[11:16].replace(":", "h")


# ============================================================================
# PARSEAMENTO DA PLANILHA
# ============================================================================

COLUNAS_ESPERADAS = ["Data", "Cliente", "Telefone", "Serviço", "Sala", "Situação", "Realizado por"]


def parsear_planilha(arquivo_upload, unidade: str) -> tuple:
    """
    Retorna (df_agrupado, avisos, erros).
    df_agrupado tem 1 linha por telefone único.
    """
    avisos = []
    erros = []

    try:
        df = pd.read_excel(arquivo_upload, sheet_name=0)
    except Exception as e:
        erros.append(f"Não consegui ler o arquivo XLSX: {e}")
        return None, avisos, erros

    # Verifica colunas
    faltando = [c for c in COLUNAS_ESPERADAS if c not in df.columns]
    if faltando:
        erros.append(f"Colunas faltando na planilha: {faltando}")
        return None, avisos, erros

    # Filtro: só 'Realizado'
    if "Situação" in df.columns:
        antes = len(df)
        df = df[df["Situação"].astype(str).str.strip().str.lower() == "realizado"].copy()
        depois = len(df)
        if antes != depois:
            avisos.append(f"⚠️ {antes - depois} linha(s) com situação diferente de 'Realizado' foram ignoradas.")

    if df.empty:
        erros.append("Depois de filtrar por 'Realizado', a planilha ficou vazia.")
        return None, avisos, erros

    # Limpa telefone
    df["Telefone"] = df["Telefone"].astype(str).str.replace(r"\D", "", regex=True)
    df = df[df["Telefone"].str.len() >= 10].copy()
    if df.empty:
        erros.append("Nenhum telefone válido depois da limpeza.")
        return None, avisos, erros

    # Agrupamento por telefone
    grupos = df.groupby("Telefone", sort=False)
    linhas_agrupadas = []

    for tel, grupo in grupos:
        primeira = grupo.iloc[0]
        servicos = grupo["Serviço"].tolist()
        profissional_raw = str(primeira.get("Realizado por", ""))
        nome_raw = str(primeira.get("Cliente", ""))
        data_sessao_raw = primeira.get("Data")

        # Parse data
        try:
            if isinstance(data_sessao_raw, str):
                data_dt = pd.to_datetime(data_sessao_raw, dayfirst=True)
            else:
                data_dt = pd.Timestamp(data_sessao_raw)
        except Exception:
            data_dt = None

        linhas_agrupadas.append({
            "telefone": tel,
            "nome_completo": nome_raw,
            "nome": primeiro_nome_cliente(nome_raw),
            "profissional_completo": profissional_raw,
            "profissional": primeiro_ultimo_profissional(profissional_raw),
            "unidade": unidade,
            "data_sessao": data_dt.date() if data_dt is not None else None,
            "hora_sessao": formatar_hora(data_dt) if data_dt is not None else None,
            "data_fmt": formatar_data(data_dt) if data_dt is not None else "?",
            "areas": formatar_areas(servicos),
            "servicos_originais": servicos,
            "qtd_servicos": len(servicos),
        })

    df_ag = pd.DataFrame(linhas_agrupadas)
    return df_ag, avisos, erros


# ============================================================================
# ENVIO META (template)
# ============================================================================

def enviar_template_pos(telefone: str, nome: str, data: str, hora: str, areas: str, profissional: str) -> tuple:
    """
    Retorna (sucesso, wamid_ou_erro).
    """
    try:
        token = st.secrets["TOKEN_META"]
    except Exception:
        return False, "TOKEN_META ausente em st.secrets"

    url = f"https://graph.facebook.com/{META_API_VERSION}/{PHONE_ID_POS}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": str(telefone),
        "type": "template",
        "template": {
            "name": TEMPLATE_NOME,
            "language": {"code": TEMPLATE_LANG},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(nome)[:60] or "cliente"},
                        {"type": "text", "text": str(data)[:20]},
                        {"type": "text", "text": str(hora)[:20]},
                        {"type": "text", "text": str(areas)[:150]},
                        {"type": "text", "text": str(profissional)[:60] or "equipe"},
                    ]
                }
            ]
        }
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code >= 200 and resp.status_code < 300:
            data = resp.json()
            wamid = None
            try:
                wamid = data["messages"][0]["id"]
            except Exception:
                pass
            return True, wamid
        else:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, f"exceção: {e}"


# ============================================================================
# INSERÇÃO NO SUPABASE
# ============================================================================

def inserir_clientes_supabase(df_ag: pd.DataFrame) -> tuple:
    """Insere clientes com status='aguardando_disparo'. Retorna (ids_inseridos, erro).

    v1.1: Antes de inserir, marca sessões ativas antigas do mesmo telefone
    como 'substituido_por_novo_disparo' pra evitar múltiplas sessões concorrentes.
    """
    sb = _get_sb()
    telefones = df_ag["telefone"].astype(str).unique().tolist()

    # ── v1.1: Finaliza sessões órfãs (mesmo telefone, ainda ativas) ──
    sessoes_finalizadas = 0
    try:
        r_ativos = (
            sb.table("pos_atendimento_clientes")
              .select("id,telefone,status")
              .in_("telefone", telefones)
              .in_("status", ["aguardando_disparo", "template_enviado", "tudo_otimo_pendente"])
              .execute()
        )
        ativos = r_ativos.data or []
        if ativos:
            ids_a_finalizar = [c["id"] for c in ativos]
            (sb.table("pos_atendimento_clientes")
               .update({
                   "status": "substituido_por_novo_disparo",
                   "ultima_atualizacao": datetime.now(TZ_SP).isoformat(),
               })
               .in_("id", ids_a_finalizar)
               .execute())
            sessoes_finalizadas = len(ids_a_finalizar)

            # Loga cada finalização (append-only, pra rastreio)
            logs = []
            agora_iso = datetime.now(TZ_SP).isoformat()
            for c in ativos:
                logs.append({
                    "data_hora": agora_iso,
                    "telefone": c["telefone"],
                    "nome": None,
                    "tipo_mensagem": "sistema",
                    "conteudo": "[novo disparo substituiu sessão ativa]",
                    "status_antes": c["status"],
                    "status_depois": "substituido_por_novo_disparo",
                    "unidade": None,
                    "observacao": f"🔄 Sessão finalizada por novo upload da planilha (id antigo: {c['id']})",
                    "cliente_id": c["id"],
                })
            if logs:
                sb.table("pos_atendimento_log").insert(logs).execute()
    except Exception as e:
        # Não bloqueia o disparo se dedup falhar — só avisa
        st.warning(f"⚠️ Não consegui finalizar sessões antigas: {e}")

    if sessoes_finalizadas > 0:
        st.info(f"🔄 {sessoes_finalizadas} sessão(ões) ativa(s) do(s) mesmo(s) telefone(s) foram finalizadas antes do novo disparo.")

    # ── INSERT do novo lote ──
    linhas = []
    for _, row in df_ag.iterrows():
        linhas.append({
            "telefone":              str(row["telefone"]),
            "nome":                  row["nome"],
            "nome_completo":         row["nome_completo"],
            "profissional":          row["profissional"],
            "profissional_completo": row["profissional_completo"],
            "unidade":               row["unidade"],
            "data_sessao":           str(row["data_sessao"]) if row["data_sessao"] else None,
            "hora_sessao":           row["hora_sessao"],
            "areas":                 row["areas"],
            "areas_lista":           json.dumps([limpar_servico(s) for s in row["servicos_originais"]], ensure_ascii=False),
            "servicos_originais":    json.dumps(row["servicos_originais"], ensure_ascii=False),
            "status":                "aguardando_disparo",
        })
    try:
        r = sb.table("pos_atendimento_clientes").insert(linhas).execute()
        ids = [d["id"] for d in (r.data or [])]
        return ids, None
    except Exception as e:
        return [], str(e)


def gravar_disparo_historico(dados: dict) -> int:
    """Cria linha em pos_atendimento_disparos_historico. Retorna id."""
    try:
        sb = _get_sb()
        r = sb.table("pos_atendimento_disparos_historico").insert(dados).execute()
        return r.data[0]["id"] if r.data else None
    except Exception as e:
        st.error(f"⚠️ Erro ao gravar histórico: {e}")
        return None


def atualizar_disparo_historico(id_disparo: int, campos: dict):
    try:
        sb = _get_sb()
        sb.table("pos_atendimento_disparos_historico").update(campos).eq("id", id_disparo).execute()
    except Exception:
        pass


def atualizar_cliente(id_cliente: int, campos: dict):
    try:
        sb = _get_sb()
        sb.table("pos_atendimento_clientes").update(campos).eq("id", id_cliente).execute()
    except Exception:
        pass


def registrar_log(telefone: str, nome: str, tipo: str, conteudo: str,
                  status_antes: str, status_depois: str, unidade: str,
                  observacao: str, cliente_id: int = None):
    try:
        sb = _get_sb()
        sb.table("pos_atendimento_log").insert({
            "data_hora": datetime.now(TZ_SP).isoformat(),
            "telefone": telefone,
            "nome": nome,
            "tipo_mensagem": tipo,
            "conteudo": conteudo[:500] if conteudo else None,
            "status_antes": status_antes,
            "status_depois": status_depois,
            "unidade": unidade,
            "observacao": observacao,
            "cliente_id": cliente_id,
        }).execute()
    except Exception:
        pass


# ============================================================================
# UI
# ============================================================================

def render_aba_pos_disparar():
    # ══════════════════════════════════════════════════════════════════
    # RESET — clique em "🔄 Fazer novo disparo" seta pos_reset_pending=True
    # É processado AQUI, antes de qualquer widget renderizar,
    # pra garantir estado limpo do zero.
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.get("pos_reset_pending"):
        # Incrementa gen do uploader antes de matar chaves
        st.session_state["pos_uploader_gen"] = st.session_state.get("pos_uploader_gen", 0) + 1
        gen_novo = st.session_state["pos_uploader_gen"]

        # Mata TUDO que começa com pos_ EXCETO o gen novo
        for k in list(st.session_state.keys()):
            if k.startswith("pos_") and k != "pos_uploader_gen":
                del st.session_state[k]

        st.cache_data.clear()
        st.rerun()

    st.markdown("## 🚀 Disparar Pós-atendimento")
    st.caption("Upload da planilha do dia anterior (UNO). Sistema envia template Meta aprovado para cada cliente único.")

    # ══════════════════════════════════════════════════════════════════
    # BOTÃO "NOVO DISPARO" — só aparece quando último disparo finalizou
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.get("pos_disparo_finalizado"):
        st.info("✅ Último disparo finalizado. Clique abaixo pra iniciar um novo.")
        if st.button("🔄 Fazer novo disparo",
                     type="primary",
                     use_container_width=True,
                     key="pos_btn_novo_disparo"):
            st.session_state.pos_reset_pending = True
            st.rerun()
        return

    # ── Estado do sistema ──
    cfg = _get_config_pos()
    if not cfg:
        st.error("⚠️ Não consegui ler configurações do Supabase. Verifique conexão.")
        return

    if not cfg.get("pos_habilitado"):
        st.error("🔴 **Robô Pós-atendimento está DESABILITADO** — flag `pos_habilitado=false` no Supabase.")
        st.caption("Pra habilitar: `UPDATE configuracoes SET pos_habilitado=true WHERE id=1;`")
        return

    if cfg.get("modo_manutencao"):
        st.error("🔴 **MODO MANUTENÇÃO ATIVO** — todos os robôs estão pausados.")
        return

    hora_atual = datetime.now(TZ_SP).hour
    inicio = cfg.get("pos_janela_hora_inicio", 8)
    fim = cfg.get("pos_janela_hora_fim", 19)
    dentro_janela = inicio <= hora_atual < fim

    if not dentro_janela:
        st.warning(f"⚠️ Fora do horário comercial ({inicio}h-{fim}h). Hora atual: {hora_atual}h. Você pode preparar o disparo, mas o botão de envio ficará bloqueado até {inicio}h.")

    # ── Toggle Unidade ──
    st.markdown("### 1. Selecione a unidade")
    col_u1, col_u2 = st.columns(2)

    if "pos_unidade" not in st.session_state:
        st.session_state.pos_unidade = None

    with col_u1:
        if st.button("📍 Mogi das Cruzes",
                     type="primary" if st.session_state.pos_unidade == "Mogi das Cruzes" else "secondary",
                     use_container_width=True, key="pos_btn_mogi"):
            st.session_state.pos_unidade = "Mogi das Cruzes"
            st.rerun()
    with col_u2:
        if st.button("📍 Suzano",
                     type="primary" if st.session_state.pos_unidade == "Suzano" else "secondary",
                     use_container_width=True, key="pos_btn_suzano"):
            st.session_state.pos_unidade = "Suzano"
            st.rerun()

    if not st.session_state.pos_unidade:
        st.info("👆 Selecione a unidade antes de fazer upload.")
        return

    st.success(f"✅ Unidade selecionada: **{st.session_state.pos_unidade}**")

    # ── Mostrar coordenadora que receberá alertas dessa unidade ──
    if st.session_state.pos_unidade == "Mogi das Cruzes":
        coord_tel = cfg.get("pos_coord_mogi_telefone", "5511974485859")
        coord_nome = "Coordenadora Mogi"
    else:
        coord_tel = cfg.get("pos_coord_suzano_telefone", "5511913194989")
        coord_nome = "Coordenadora Suzano"

    st.info(
        f"🔔 **Alertas de problemas e pedidos de cupom serão enviados para:**  \n"
        f"📞 {coord_nome} · **+{coord_tel}**  \n"
        f"_(Pra alterar, vá na aba **⚙️ Configurações**)_"
    )

    # ── Upload XLSX ──
    st.markdown("### 2. Upload da planilha")

    # ── Aviso das colunas esperadas ──
    st.warning(
        "⚠️ **A planilha DEVE conter exatamente estas 7 colunas** (nomes exatos, "
        "com acentos):\n\n"
        "| Coluna | Exemplo |\n"
        "|---|---|\n"
        "| **Data** | `02/07/2026 09:00` (data + hora) |\n"
        "| **Cliente** | `Ingrid Dos Santos Neves` |\n"
        "| **Telefone** | `5511982718573` (só números) |\n"
        "| **Serviço** | `F - Depilação de Axilas cortesia` |\n"
        "| **Sala** | `Procedimento 1` |\n"
        "| **Situação** | `Realizado` (só \"Realizado\" será processado) |\n"
        "| **Realizado por** | `Clícia Maria Medeiros Costa` |\n\n"
        "💡 Este é o formato **padrão de exportação do UNO** — se exportou de lá, "
        "provavelmente já está no formato certo. Se faltar coluna, o sistema aborta."
    )

    # Contador incrementado a cada "Novo disparo" — força o file_uploader a resetar
    if "pos_uploader_gen" not in st.session_state:
        st.session_state.pos_uploader_gen = 0

    arquivo = st.file_uploader(
        "Selecione o XLSX exportado do UNO (agendamentos do dia anterior)",
        type=["xlsx", "xls"],
        key=f"pos_uploader_{st.session_state.pos_uploader_gen}"
    )
    if not arquivo:
        st.info("Aguardando upload da planilha…")
        return

    # ── Parseamento ──
    with st.spinner("Processando planilha…"):
        df_ag, avisos, erros = parsear_planilha(arquivo, st.session_state.pos_unidade)

    for aviso in avisos:
        st.warning(aviso)
    for erro in erros:
        st.error(f"❌ {erro}")

    if df_ag is None or df_ag.empty:
        return

    # ── Métricas ──
    st.markdown("### 3. Preview")

    total_clientes = len(df_ag)
    total_servicos = int(df_ag["qtd_servicos"].sum())

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("👥 Clientes únicos", total_clientes)
    col_m2.metric("💼 Serviços totais", total_servicos)
    col_m3.metric("📍 Unidade", st.session_state.pos_unidade.replace("Mogi das Cruzes", "Mogi"))

    # ── Amostra da mensagem ──
    st.markdown("**📱 Preview da mensagem (primeiro cliente):**")
    primeira = df_ag.iloc[0]

    preview_msg = (
        f"Olá **{primeira['nome']}**, tudo bem? 🥰\n\n"
        f"Vimos que dia **{primeira['data_fmt']}** às **{primeira['hora_sessao']}**, "
        f"você realizou sua sessão nas áreas **{primeira['areas']}**, com a **{primeira['profissional']}** conosco!\n\n"
        f"Passando para saber o que você achou do atendimento?\n"
        f"Se ficou tudo bem com as áreas realizadas?\n"
        f"E como está seu resultado? 💚\n\n"
        f"*[🌟 Tudo ótimo] [⚠️ Problema com atendimento] [❌ Resultado ruim]*"
    )
    st.info(preview_msg)

    # ── Tabela completa ──
    with st.expander(f"📋 Ver lista completa dos {total_clientes} clientes"):
        df_show = df_ag[["nome", "telefone", "data_fmt", "hora_sessao", "areas", "profissional", "qtd_servicos"]].copy()
        df_show = df_show.rename(columns={
            "nome": "Cliente",
            "telefone": "Telefone",
            "data_fmt": "Data",
            "hora_sessao": "Hora",
            "areas": "Áreas",
            "profissional": "Profissional",
            "qtd_servicos": "Qtd serv"
        })
        st.dataframe(df_show, use_container_width=True, hide_index=True)

    # ── Botão de disparo ──
    st.markdown("### 4. Disparar")

    if not dentro_janela:
        st.error(f"🔴 Fora do horário comercial ({inicio}h-{fim}h). Disparo bloqueado.")
        st.button("🚀 Disparar", disabled=True, use_container_width=True)
        return

    # ── AVISO CRÍTICO: janela 24h da coordenadora ──
    st.warning(
        f"""
⚠️ **IMPORTANTE — abra a janela 24h da coordenadora antes de disparar**

Pra que os alertas de _problema com atendimento_, _resultado ruim_ e _pedidos de cupom_ cheguem em **{coord_nome}** (`+{coord_tel}`), ela precisa ter mandado qualquer mensagem (ex: "oi") pro robô **97502-5297** nas últimas 24h.

**Sem essa janela aberta, os alertas VÃO FALHAR** (Meta bloqueia mensagens fora do template pra números que não iniciaram conversa).

📋 **Passo a passo antes de disparar:**
1. Peça pra **{coord_nome}** mandar "oi" pelo WhatsApp pro robô **+5511975025297**
2. Espere até ver a mensagem entregar (✓✓)
3. Marque a caixa abaixo confirmando
        """
    )

    # ── Botão de atalho pra abrir WhatsApp Web ──
    col_wa1, col_wa2 = st.columns([1, 1])
    with col_wa1:
        wa_link_coord = f"https://wa.me/{coord_tel}?text=Olá!%20Aqui%20está%20o%20link%20do%20robô%20Pós-atendimento%3A%20https%3A%2F%2Fwa.me%2F5511975025297%20%2D%20mande%20%22oi%22%20pra%20abrir%20a%20janela%20de%20alertas."
        st.link_button(
            f"💬 Chamar {coord_nome} no WhatsApp",
            wa_link_coord,
            use_container_width=True
        )
    with col_wa2:
        wa_link_robo = "https://wa.me/5511975025297?text=oi"
        st.link_button(
            "🤖 Abrir chat com o robô (mandar 'oi')",
            wa_link_robo,
            use_container_width=True
        )

    janela_coord_ok = st.checkbox(
        f"✅ Confirmo que {coord_nome} (+{coord_tel}) já mandou mensagem pro robô nas últimas 24h",
        key="pos_janela_coord_ok"
    )

    if not janela_coord_ok:
        st.button(
            f"🚀 Disparar template para {total_clientes} cliente(s)",
            disabled=True,
            use_container_width=True,
            help="Marque a caixa acima confirmando que a janela 24h da coordenadora está aberta"
        )
        return

    # Confirmação dupla
    if "pos_confirmar_disparo" not in st.session_state:
        st.session_state.pos_confirmar_disparo = False

    if not st.session_state.pos_confirmar_disparo:
        if st.button(f"🚀 Disparar template para {total_clientes} cliente(s)",
                     type="primary", use_container_width=True, key="pos_btn_disp_1"):
            st.session_state.pos_confirmar_disparo = True
            st.rerun()
    else:
        st.warning(f"⚠️ Confirmar envio para **{total_clientes} clientes** da unidade **{st.session_state.pos_unidade}**?")
        col_ok, col_cancel = st.columns(2)
        with col_ok:
            if st.button("✅ Sim, disparar agora", type="primary", use_container_width=True, key="pos_confirm_yes"):
                _executar_disparo(df_ag, st.session_state.pos_unidade, arquivo.name)
                st.session_state.pos_confirmar_disparo = False
        with col_cancel:
            if st.button("❌ Cancelar", use_container_width=True, key="pos_confirm_no"):
                st.session_state.pos_confirmar_disparo = False
                st.rerun()


def _executar_disparo(df_ag: pd.DataFrame, unidade: str, nome_arquivo: str):
    """Executa o disparo real. Cria registros no Supabase + envia templates Meta."""

    # ── 1. Cria linhas no pos_atendimento_clientes ──
    with st.spinner("Cadastrando clientes no Supabase..."):
        ids, erro = inserir_clientes_supabase(df_ag)
        if erro:
            st.error(f"❌ Falha ao inserir clientes: {erro}")
            return
        df_ag = df_ag.reset_index(drop=True)
        df_ag["cliente_id"] = ids

    st.success(f"✅ {len(ids)} clientes cadastrados no Supabase.")

    # ── 2. Cria linha em pos_atendimento_disparos_historico ──
    datas_sessoes = df_ag["data_fmt"].value_counts()
    data_sessoes_str = ", ".join([f"{v}x {k}" for k, v in datas_sessoes.items()])

    id_disparo = gravar_disparo_historico({
        "criado_em":             datetime.now(TZ_SP).isoformat(),
        "unidade":               unidade,
        "arquivo":               nome_arquivo,
        "data_sessoes":          data_sessoes_str,
        "total_linhas_planilha": int(df_ag["qtd_servicos"].sum()),
        "total_clientes_unicos": len(df_ag),
        "duplicatas_ignoradas":  0,
        "template_enviados_ok":  0,
        "erros_envio":           0,
        "erros_envio_detalhes":  None,
        "janela_horario_ok":     True,
        "fase":                  "DISPARANDO",
    })

    # ── 3. Loop de envio ──
    st.markdown("---")
    st.markdown("### 📤 Enviando templates...")

    progress = st.progress(0.0)
    status_text = st.empty()

    total = len(df_ag)
    sucessos = 0
    erros_lista = []

    for i, row in df_ag.iterrows():
        status_text.text(f"Enviando {i+1}/{total} — {row['nome']} ({row['telefone']})...")
        progress.progress((i+1) / total)

        ok, resposta = enviar_template_pos(
            telefone=row["telefone"],
            nome=row["nome"],
            data=row["data_fmt"],
            hora=row["hora_sessao"],
            areas=row["areas"],
            profissional=row["profissional"]
        )

        if ok:
            sucessos += 1
            atualizar_cliente(row["cliente_id"], {
                "status":     "template_enviado",
                "disparo_ts": datetime.now(TZ_SP).isoformat(),
                "wamid":      resposta,
            })
            registrar_log(
                row["telefone"], row["nome"], "saida_template",
                f"[template {TEMPLATE_NOME}]",
                "aguardando_disparo", "template_enviado",
                unidade,
                f"✅ Template enviado ({row['data_fmt']} {row['hora_sessao']})",
                cliente_id=row["cliente_id"]
            )
        else:
            erros_lista.append(f"{row['nome']} ({row['telefone']}): {resposta}")
            atualizar_cliente(row["cliente_id"], {
                "status": "falha_envio"
            })
            registrar_log(
                row["telefone"], row["nome"], "erro_envio",
                str(resposta)[:400],
                "aguardando_disparo", "falha_envio",
                unidade,
                f"❌ FALHA NO ENVIO: {str(resposta)[:200]}",
                cliente_id=row["cliente_id"]
            )

        # Rate limit — pequena pausa entre envios
        time.sleep(0.3)

    # ── 4. Finaliza histórico ──
    atualizar_disparo_historico(id_disparo, {
        "fase":                 "FINALIZADO",
        "finalizado_em":        datetime.now(TZ_SP).isoformat(),
        "template_enviados_ok": sucessos,
        "erros_envio":          len(erros_lista),
        "erros_envio_detalhes": "\n".join(erros_lista[:20]) if erros_lista else None,
    })

    # ── 5. Resumo ──
    progress.empty()
    status_text.empty()

    st.markdown("---")
    st.markdown("### 🎉 Disparo finalizado")

    col_r1, col_r2, col_r3 = st.columns(3)
    col_r1.metric("✅ Enviados", sucessos)
    col_r2.metric("❌ Erros", len(erros_lista))
    col_r3.metric("📊 Taxa sucesso", f"{sucessos/total*100:.1f}%" if total > 0 else "0%")

    if erros_lista:
        with st.expander(f"❌ Ver {len(erros_lista)} erro(s)"):
            for e in erros_lista:
                st.error(e)

    # Limpa cache pra próximo uso
    st.cache_data.clear()

    st.success("✅ Disparo registrado. Clientes agora estão em `template_enviado`, aguardando resposta.")

    # Sinaliza que o disparo terminou — o botão "Novo disparo" será renderizado
    # no render_aba_pos_disparar (fora de _executar_disparo) pra evitar
    # conflitos de estado com pos_confirmar_disparo/pos_confirm_yes
    st.session_state.pos_disparo_finalizado = True
