import hashlib
import re
import time
import random
from datetime import datetime
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException


# ─── Helpers de extracao ────────────────────────────────────────────────────────

def _normalize_preco_text(texto):
    """Normaliza string de valor e converte para float."""
    if not texto:
        return None

    # Normaliza caracteres de espaco e remove multiplicadores comuns
    limpo = texto.replace("\xa0", "").strip()

    # Remove prefixo de moeda caso exista, mas mantem o numero em si.
    limpo = re.sub(r'(?i)\b(brl|usd|eur|r\$)\b', '', limpo)

    # Se propaga apenas o grupo numerico principal (ex: "R$ 1.234,56" -> "1.234,56").
    m = re.search(r'([\d\.\,]+)', limpo)
    if m:
        limpo = m.group(1)
    else:
        return None

    # Remove separador de milhar e normaliza decimal
    limpo = re.sub(r'\.(?=\d{3}(?:\D|$))', '', limpo)
    limpo = limpo.replace(',', '.')

    # Mantem apenas digitos e ponto
    limpo = re.sub(r'[^\d.]', '', limpo).strip()

    if not limpo:
        return None
    if limpo.count('.') > 2:
        return None

    try:
        return float(limpo)
    except ValueError:
        return None


def _extrair_preco(card, seletor): 
    """
    Extrai e converte o preco de um card para float.
    Trata formatos: 'R$ 1.234,56', '1234.56', '1.234'.
    Retorna None se nao encontrar ou nao conseguir converter.
    """
    elements = card.find_elements(By.CSS_SELECTOR, seletor)
    if not elements:
        return None

    candidatos = []
    for el in elements:
        texto = (
            el.text
            or el.get_attribute("textContent")
            or el.get_attribute("innerText")
            or el.get_attribute("aria-label")
            or ""
        ).strip()
        if not texto:
            continue

        # Rejeita textos que claramente descrevem duracao ou horarios
        if re.search(r'\d+\s*h(?:oras?)?|\d+\s*m(?:inutos?)?|\d{1,2}:\d{2}', texto, re.IGNORECASE):
            # só aceita se tiver moeda explícita
            if not re.search(r'(?i)\b(r\$|brl|usd|eur)\b', texto):
                continue

        preco = _normalize_preco_text(texto)
        if preco is None:
            continue

        has_moeda = bool(re.search(r'(?i)\b(r\$|brl|usd|eur)\b', texto))
        candidatos.append((preco, has_moeda, texto))

    if not candidatos:
        return None

    # Prefere extracao com indicador de moeda; se houver varios, pega menor (menor tarifa)
    com_moeda = [p for p in candidatos if p[1]]
    if com_moeda:
        preco_final = min(p[0] for p in com_moeda)
    else:
        # sobrevive a valores de duracao omitidos (excluídos logo acima)
        preco_final = min(p[0] for p in candidatos)

    # Ignora resultados claramente inválidos para passagens B2C no Brasil
    if preco_final <= 0 or preco_final < 10:
        return None

    return preco_final


def _extrair_escalas(card, seletor):
    """
    Extrai o numero de escalas de um card.
    Retorna 0 para voo direto, int para escalas, None se nao encontrar.
    """
    try:
        texto = card.find_element(By.CSS_SELECTOR, seletor).text.strip().lower()
    except NoSuchElementException:
        return None

    if not texto:
        return None

    if any(p in texto for p in ["direto", "direct", "nonstop", "sem escala"]):
        return 0

    digitos = re.findall(r'\d+', texto)
    if digitos:
        return int(digitos[0])

    return 1  


# ─── FUNÇÕES DESCONTINUADAS (hash file system) ──────────────────────────────

def hash_gz_file(path) -> str:
    """Calcula MD5 de um arquivo compactado salvo para auditoria local."""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def destruir_html_log(path, hash_esperado: str = "", log_fn=None) -> bool:
    """Remove um HTML bruto apos envio, conferindo o hash quando disponivel."""
    path = Path(path)
    if not path.exists():
        if log_fn:
            log_fn(f"[LOG] Arquivo ja removido: {path.name}")
        return False
    if hash_esperado and hash_gz_file(path) != hash_esperado:
        if log_fn:
            log_fn(f"[LOG] Hash divergente, preservando: {path.name}")
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        if log_fn:
            log_fn(f"[LOG] Falha ao remover {path.name}: {e}")
        return False


def _classificar_janela(hora):
    if hora < 6:  return "madrugada (00-06h)"
    if hora < 12: return "manha (06-12h)"
    if hora < 18: return "tarde (12-18h)"
    return "noite (18-24h)"


class ColetaCancelada(Exception):
    """Interrompe a coleta atual quando o usuario clica em Parar."""


def _cancelado(should_stop):
    return bool(should_stop and should_stop())


def _verificar_cancelamento(should_stop):
    if _cancelado(should_stop):
        raise ColetaCancelada()


def sleep_interrompivel(min_s, max_s=None, enabled=True, should_stop=None):
    if not enabled:
        _verificar_cancelamento(should_stop)
        return

    if max_s is None:
        max_s = min_s

    fim = time.time() + max(0, min_s if min_s == max_s else min_s + ((max_s - min_s) * 0.5))
    if max_s != min_s:
        fim = time.time() + random.uniform(min_s, max_s)

    while time.time() < fim:
        _verificar_cancelamento(should_stop)
        time.sleep(min(0.3, fim - time.time()))
    _verificar_cancelamento(should_stop)


def _gerar_id(plataforma, origem, destino, data_voo_str):
    raw = f"{plataforma}{origem}{destino}{data_voo_str}{datetime.utcnow().isoformat()}"
    return hashlib.md5(raw.encode()).hexdigest()[:10].upper()


# ─── Extrator do MENOR PRECO de uma lista de cards ──────────────────────────

def extrair_menor_preco(cards, seletor_preco, log_fn=None):
    """
    Extrai o MENOR preço de TODOS os cards disponíveis.
    
    Retorna:
        dict: {
            'preco': float (menor preço encontrado),
            'card_index': int (índice do card com menor preço),
            'total_precos': int (quantos preços foram encontrados)
        }
        ou {} se nenhum preço válido encontrado
    """
    precos_encontrados = []
    
    def _log(msg):
        if log_fn:
            log_fn(msg)
    
    for idx, card in enumerate(cards):
        try:
            preco = _extrair_preco(card, seletor_preco)
            if preco is not None:
                precos_encontrados.append({
                    'preco': preco,
                    'card_index': idx
                })
        except Exception as e:
            _log(f"[PRECO] Erro ao extrair preço do card {idx}: {e}")
    
    if not precos_encontrados:
        return {}
    
    # Ordena por preço (crescente) e retorna o MENOR
    melhor = min(precos_encontrados, key=lambda x: x['preco'])
    resultado = {
        'preco': melhor['preco'],
        'card_index': melhor['card_index'],
        'total_precos': len(precos_encontrados),
    }
    
    _log(f"[PRECO] Coletados {len(precos_encontrados)} preço(s) | Menor: R$ {melhor['preco']:.2f} (card #{melhor['card_index']})")
    
    return resultado


# ─── Montagem do registro padrao ────────────────────────────────────────────

def _montar_registro(plataforma, url, origem, destino, data_voo_str, antecedencia,
                     preco_num, hora_saida, hora_chegada,
                     modo_privacidade, user_agent_label,
                     agora, experimento_id, captcha_detectado=False):
    """
    Monta o dicionario padrao de um registro de coleta.
    Estrutura alinhada perfeitamente com COLUNAS_DATASET.
    """
    return {
        "id_coleta":            _gerar_id(plataforma, origem, destino, data_voo_str),
        "data_hora_local":      agora.strftime("%Y-%m-%d %H:%M:%S"),
        "janela_horario":       _classificar_janela(agora.hour),
        "plataforma":           plataforma,
        "url_consultada":       url,
        "origem":               origem.upper(),
        "destino":              destino.upper(),
        "data_voo":             data_voo_str,
        "dias_antecedencia":    antecedencia,
        "preco_brl":            preco_num,
        "hora_saida":           hora_saida,
        "hora_chegada":         hora_chegada,
        "modo_privacidade":     modo_privacidade,
        "user_agent_label":     user_agent_label,
        "captcha_detectado":    captcha_detectado,
        "dia_semana_busca":     agora.strftime("%A"),
        "experimento_id":       experimento_id,
    }
