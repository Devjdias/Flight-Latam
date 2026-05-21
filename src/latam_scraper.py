import re
import time
import unicodedata
from datetime import datetime

from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchWindowException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from scrapers_utils import (
    _montar_registro,
    extrair_menor_preco,
    ColetaCancelada,
    _verificar_cancelamento,
    sleep_interrompivel,
    _normalize_preco_text,
)


URL_TEMPLATE = (
    "https://www.latamairlines.com/br/pt/oferta-voos"
    "?origin={origem}"
    "&outbound={data}T00%3A00%3A00.000Z"
    "&destination={destino}"
    "&adt=1&chd=0&inf=0&trip=OW&cabin=Y&redemption=false&sort=RECOMMENDED"
)

SEL = {
    "card_voo": "[data-testid^='wrapper-card-flight-']",
    "card_voo_alt": "div[class*='cardFlight'], div[class*='FlightCard']",
    "preco": "[data-testid='wrapper-currency-amount'], [data-testid='wrapper-currency-amount'] span",
    "preco_alt": "span[class*='price'], span[class*='Price'], div[class*='amount']",
    "hora_saida": "[data-testid$='-origin'] span",
    "hora_chegada": "[data-testid$='-destination'] span",
    "spinner": "[class*='loading'], [class*='spinner'], [aria-label*='Loading']",
}

SEL_COOKIES = [
    "#onetrust-accept-btn-handler",
    "button[id*='accept']",
    "button[class*='accept']",
]

SEL_BLOQUEIO = [
    "iframe[src*='recaptcha'][src*='bframe']",
    "[id='challenge-stage']",
    "[id='cf-challenge-running']",
]

MAX_TENTATIVAS_CARREGAMENTO = 4
TIMEOUT_CARDS_S = 55
TIMEOUT_SPINNER_S = 20
HTML_MINIMO_UTIL = 5000

def _sem_acento(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )

def _snapshot_pagina(driver):
    try:
        html = driver.page_source or ""
    except Exception:
        html = ""
    try:
        titulo = driver.title or ""
    except Exception:
        titulo = ""
    try:
        url_atual = driver.current_url or ""
    except Exception:
        url_atual = ""

    texto = f"{titulo}\n{html}".lower()
    return {
        "titulo": titulo.strip() or "N/D",
        "url": url_atual,
        "html_len": len(html),
        "texto": texto,
        "texto_sem_acento": _sem_acento(texto),
    }

def _detectar_bloqueio(driver):
    snap = _snapshot_pagina(driver)
    texto = snap["texto"]
    texto_sem_acento = snap["texto_sem_acento"]

    if "acesso negado" in texto_sem_acento or "access denied" in texto:
        return True

    for sel in SEL_BLOQUEIO:
        try:
            if driver.find_elements(By.CSS_SELECTOR, sel):
                return True
        except Exception:
            pass
    return False

def _pagina_lenta(driver):
    snap = _snapshot_pagina(driver)
    texto = snap["texto"]
    texto_sem_acento = snap["texto_sem_acento"]
    return (
        "busca esta demorando" in texto_sem_acento
        or "search is taking longer" in texto
        or "tivemos um problema com os resultados" in texto_sem_acento
        or "tempo-resultados-busca" in texto
    )

def _diagnosticar_estado_pagina(driver):
    snap = _snapshot_pagina(driver)
    texto = snap["texto"]
    texto_sem_acento = snap["texto_sem_acento"]

    motivos = []
    if snap["html_len"] < HTML_MINIMO_UTIL:
        motivos.append(f"HTML muito pequeno ({snap['html_len']} bytes)")
    if "acesso negado" in texto_sem_acento or "access denied" in texto:
        motivos.append("possivel acesso negado")
    if "configuracoes de cookies" in texto_sem_acento:
        motivos.append("banner de cookies bloqueando a pagina")
    if _pagina_lenta(driver):
        motivos.append("pagina lenta da LATAM")
    indisponibilidade = any(p in texto_sem_acento for p in (
        "nao encontramos voos",
        "nao ha voos",
        "sem voos",
        "no flights",
        "we could not find flights",
    ))
    if indisponibilidade and not _pagina_lenta(driver):
        motivos.append("mensagem de indisponibilidade de voos")
    if "oferta-voos" not in snap["url"]:
        motivos.append(f"URL inesperada: {snap['url']}")

    if not motivos:
        motivos.append("cards/precos nao apareceram dentro do tempo limite")

    return snap, "; ".join(motivos)

def _clicar_elemento(driver, elemento, should_stop=None):
    _verificar_cancelamento(should_stop)
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        elemento,
    )
    sleep_interrompivel(0.4, 0.8, enabled=True, should_stop=should_stop)
    _verificar_cancelamento(should_stop)
    try:
        elemento.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elemento)

def _fechar_banner_cookies(driver, log_fn, modo_privacidade, should_stop=None):
    _verificar_cancelamento(should_stop)
    snap = _snapshot_pagina(driver)
    if "configuracoes de cookies" not in snap["texto_sem_acento"]:
        return False

    preferir_rejeitar = modo_privacidade != "Normal (com cookies)"
    textos_preferidos = (
        ["recusar todos os cookies", "rejeitar todos", "reject all"]
        if preferir_rejeitar
        else ["aceite todos os cookies", "aceitar todos", "accept all"]
    )
    acao = "rejeitado" if preferir_rejeitar else "aceito"

    for texto_botao in textos_preferidos:
        xpath = (
            "//button[contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', "
            "'abcdefghijklmnopqrstuvwxyzáàâãéèêíìîóòôõúùûç'), "
            f"'{texto_botao}')]"
        )
        try:
            botoes = driver.find_elements(By.XPATH, xpath)
            for botao in botoes:
                _verificar_cancelamento(should_stop)
                if botao.is_displayed() and botao.is_enabled():
                    _clicar_elemento(driver, botao, should_stop=should_stop)
                    log_fn(f"Banner de cookies {acao} pelo texto do botao.")
                    sleep_interrompivel(1.0, 2.0, enabled=True, should_stop=should_stop)
                    return True
        except Exception:
            pass

    for seletor in SEL_COOKIES:
        try:
            botoes = driver.find_elements(By.CSS_SELECTOR, seletor)
            for botao in botoes:
                _verificar_cancelamento(should_stop)
                if botao.is_displayed() and botao.is_enabled():
                    _clicar_elemento(driver, botao, should_stop=should_stop)
                    log_fn("Banner de cookies fechado pelo seletor CSS.")
                    sleep_interrompivel(1.0, 2.0, enabled=True, should_stop=should_stop)
                    return True
        except Exception:
            pass

    xpaths = [
        "//button[contains(translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', "
        "'abcdefghijklmnopqrstuvwxyzáàâãéèêíìîóòôõúùûç'), "
        "'aceite todos os cookies')]",
        "//button[contains(translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', "
        "'abcdefghijklmnopqrstuvwxyzáàâãéèêíìîóòôõúùûç'), "
        "'aceitar todos')]",
        "//button[contains(translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
        "'accept all')]",
    ]

    for xpath in xpaths:
        try:
            botoes = driver.find_elements(By.XPATH, xpath)
            for botao in botoes:
                _verificar_cancelamento(should_stop)
                if botao.is_displayed() and botao.is_enabled():
                    _clicar_elemento(driver, botao, should_stop=should_stop)
                    log_fn("Banner de cookies fechado pelo texto do botao.")
                    sleep_interrompivel(1.0, 2.0, enabled=True, should_stop=should_stop)
                    return True
        except Exception:
            pass

    log_fn("Banner de cookies detectado, mas nao foi possivel clicar no botao.")
    return False

def _clicar_refazer_busca(driver, log_fn, should_stop=None):
    """Tenta sair da tela de erro de resultados da LATAM sem abandonar a rota."""
    _verificar_cancelamento(should_stop)
    candidatos = [
        (By.CSS_SELECTOR, "[data-testid='back-button']"),
        (
            By.XPATH,
            "//button[contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÃÃ€Ã‚ÃƒÃ‰ÃˆÃŠÃÃŒÃŽÃ“Ã’Ã”Ã•ÃšÃ™Ã›Ã‡', "
            "'abcdefghijklmnopqrstuvwxyzÃ¡Ã Ã¢Ã£Ã©Ã¨ÃªÃ­Ã¬Ã®Ã³Ã²Ã´ÃµÃºÃ¹Ã»Ã§'), "
            "'realizar outra busca')]",
        ),
        (
            By.XPATH,
            "//button[contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
            "'try again')]",
        ),
    ]

    for by, seletor in candidatos:
        try:
            for botao in driver.find_elements(by, seletor):
                _verificar_cancelamento(should_stop)
                if botao.is_displayed() and botao.is_enabled():
                    log_fn("Tela de erro da LATAM detectada; clicando em 'Realizar outra busca'.")
                    _clicar_elemento(driver, botao, should_stop=should_stop)
                    sleep_interrompivel(4, 7, enabled=True, should_stop=should_stop)
                    return True
        except Exception:
            pass

    return False

def _url_retry(url, tentativa):
    separador = "&" if "?" in url else "?"
    return f"{url}{separador}_retry={tentativa}_{int(time.time())}"

def _texto_elemento(el):
    return (
        el.text
        or el.get_attribute("textContent")
        or el.get_attribute("innerText")
        or el.get_attribute("aria-label")
        or ""
    ).strip()

def _texto_por_seletor(card, seletor):
    try:
        for el in card.find_elements(By.CSS_SELECTOR, seletor):
            texto = _texto_elemento(el)
            if texto:
                return texto
    except Exception:
        pass
    return "N/D"

def _texto_card(card):
    return _texto_elemento(card)

def _hora_por_regex(texto, padrao):
    m = re.search(padrao, _sem_acento(texto), flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return "N/D"

def _extrair_hora(card, seletor, fallback_texto, fallback_padrao):
    texto = _texto_por_seletor(card, seletor)
    m = re.search(r"\b\d{1,2}:\d{2}\b", texto)
    if m:
        return m.group(0)
    return _hora_por_regex(fallback_texto, fallback_padrao)

def _extrair_horarios_por_texto(texto):
    horarios = re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", texto)
    normalizados = []
    for hora, minuto in horarios:
        valor = f"{int(hora):02d}:{minuto}"
        if valor not in normalizados:
            normalizados.append(valor)
    return normalizados

def _extrair_menor_preco_por_texto(cards, log_fn=None):
    precos = []
    for idx, card in enumerate(cards):
        texto = _texto_card(card)
        matches = []
        for padrao in (
            r"(?:pre[c\u00e7]o.*?a partir de|brl|r\$)\s*([\d\.]+,\d{2})",
            r"(?:pre[c\u00e7]o.*?a partir de|brl|r\$)\s*([\d\.]{2,})",
        ):
            matches.extend(re.findall(padrao, texto, flags=re.IGNORECASE))
        for bruto in matches:
            preco = _normalize_preco_text(bruto)
            if preco is not None and preco >= 80:
                precos.append({"preco": preco, "card_index": idx})

    if not precos:
        return {}

    melhor = min(precos, key=lambda item: item["preco"])
    if log_fn:
        log_fn(
            f"[PRECO] Fallback por texto: {len(precos)} preco(s) | "
            f"Menor: R$ {melhor['preco']:.2f} (card #{melhor['card_index']})"
        )
    return {
        "preco": melhor["preco"],
        "card_index": melhor["card_index"],
        "total_precos": len(precos),
    }

def _extrair_menor_preco_com_fallback(cards, log_fn):
    menor_preco_info = extrair_menor_preco(cards, SEL["preco"], log_fn=log_fn)
    if menor_preco_info:
        return menor_preco_info

    log_fn("[PRECO] Seletor principal não encontrou preço; tentando seletor alternativo.")
    menor_preco_info = extrair_menor_preco(cards, SEL["preco_alt"], log_fn=log_fn)
    if menor_preco_info:
        return menor_preco_info

    return _extrair_menor_preco_por_texto(cards, log_fn=log_fn)

def _aguardar_spinner(driver, log_fn, should_stop=None):
    log_fn("Aguardando conclusão do carregamento...")
    fim = time.time() + TIMEOUT_SPINNER_S

    while time.time() < fim:
        _verificar_cancelamento(should_stop)
        try:
            if not driver.find_elements(By.CSS_SELECTOR, SEL["spinner"]):
                return
        except Exception:
            return
        time.sleep(0.5)

    log_fn("Spinner nao desapareceu dentro do limite; continuando diagnóstico.")

def _buscar_cards(driver, log_fn, log=True):
    cards = driver.find_elements(By.CSS_SELECTOR, SEL["card_voo"])
    if log:
        log_fn(f"Cards encontrados com seletor principal: {len(cards)}")
    if cards:
        return cards

    cards = driver.find_elements(By.CSS_SELECTOR, SEL["card_voo_alt"])
    if log:
        log_fn(f"Cards encontrados com seletor alternativo: {len(cards)}")
    if cards:
        return cards

    cards = _buscar_cards_por_preco(driver)
    if log:
        log_fn(f"Cards encontrados por fallback de preco: {len(cards)}")
    return cards

def _buscar_cards_por_preco(driver):
    candidatos = []
    vistos = set()
    xpaths = [
        "//*[contains(normalize-space(.), 'R$')]",
        "//*[contains(translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'BRL')]",
    ]

    for xpath in xpaths:
        try:
            elementos = driver.find_elements(By.XPATH, xpath)
        except Exception:
            continue

        for elemento in elementos[:80]:
            try:
                card = driver.execute_script(
                    """
                    let el = arguments[0];
                    for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (txt.length > 80 && txt.length < 4000 &&
                            /(?:R\\$|BRL)/i.test(txt) &&
                            /\\b\\d{1,2}:\\d{2}\\b/.test(txt)) {
                            return el;
                        }
                    }
                    return arguments[0];
                    """,
                    elemento,
                )
                if not card:
                    continue
                chave = card.id or card.get_attribute("data-testid") or card.get_attribute("outerHTML")[:200]
                if chave in vistos:
                    continue
                vistos.add(chave)
                candidatos.append(card)
            except Exception:
                continue

    return candidatos

def _aguardar_cards(driver, log_fn, should_stop=None):
    log_fn(f"Aguardando cards por até {TIMEOUT_CARDS_S}s...")
    fim = time.time() + TIMEOUT_CARDS_S

    while time.time() < fim:
        _verificar_cancelamento(should_stop)
        cards = _buscar_cards(driver, log_fn, log=False)
        if cards:
            break
        if _pagina_lenta(driver):
            log_fn("Tela de erro/lentidão da LATAM apareceu durante a espera dos cards.")
            break
        time.sleep(0.7)
    else:
        log_fn("Timeout aguardando cards; procurando mesmo assim.")
        snap, motivo = _diagnosticar_estado_pagina(driver)
        log_fn(
            f"Diagnostico parcial: {motivo} | titulo='{snap['titulo']}' | "
            f"html={snap['html_len']} bytes"
        )

    _aguardar_spinner(driver, log_fn, should_stop=should_stop)
    return _buscar_cards(driver, log_fn)

def scrape_latam(driver, origem, destino, data_voo_str,
                 experimento_id="E0", user_agent_label="Chrome Desktop (padrao)",
                 modo_privacidade="Sem cookies (perfil limpo)", log_fn=None,
                 humanize=True, should_stop=None):

    def _log(msg):
        if log_fn:
            log_fn(f"[LATAM] {msg}")

    resultados = []
    t_inicio = time.time()
    captcha = False

    url = URL_TEMPLATE.format(
        origem=origem.upper(),
        destino=destino.upper(),
        data=data_voo_str,
    )

    try:
        _verificar_cancelamento(should_stop)
        _log(f"Abrindo: {url}")
        driver.get(url)
        sleep_interrompivel(5, 9, enabled=humanize, should_stop=should_stop)
        _fechar_banner_cookies(driver, _log, modo_privacidade, should_stop=should_stop)

        _verificar_cancelamento(should_stop)
        captcha = _detectar_bloqueio(driver)
        if captcha:
            _log("Bloqueio real detectado!")

        if not captcha:
            cards = []
            for tentativa in range(1, MAX_TENTATIVAS_CARREGAMENTO + 1):
                _verificar_cancelamento(should_stop)
                if tentativa > 1:
                    espera_retry = 18 + (tentativa * 7)
                    _log(
                        f"Tentativa {tentativa}/{MAX_TENTATIVAS_CARREGAMENTO}: "
                        f"recarregando em {espera_retry}s..."
                    )
                    sleep_interrompivel(
                        espera_retry,
                        espera_retry + 5,
                        enabled=humanize,
                        should_stop=should_stop,
                    )
                    _verificar_cancelamento(should_stop)
                    driver.get(_url_retry(url, tentativa))
                    sleep_interrompivel(5, 9, enabled=humanize, should_stop=should_stop)
                    _fechar_banner_cookies(
                        driver,
                        _log,
                        modo_privacidade,
                        should_stop=should_stop,
                    )

                    _verificar_cancelamento(should_stop)
                    captcha = _detectar_bloqueio(driver)
                    if captcha:
                        _log("Bloqueio real detectado após recarregar.")
                        break

                cards = _aguardar_cards(driver, _log, should_stop=should_stop)
                if cards:
                    break

                if _pagina_lenta(driver):
                    if _clicar_refazer_busca(driver, _log, should_stop=should_stop):
                        _fechar_banner_cookies(
                            driver,
                            _log,
                            modo_privacidade,
                            should_stop=should_stop,
                        )
                        cards = _aguardar_cards(driver, _log, should_stop=should_stop)
                        if cards:
                            break
                    else:
                        _log("Tela de erro da LATAM sem botão acionável; tentando nova carga.")

                snap, motivo = _diagnosticar_estado_pagina(driver)
                _log(
                    f"Sem cards na tentativa {tentativa}: {motivo} | "
                    f"titulo='{snap['titulo']}' | html={snap['html_len']} bytes"
                )

                if (
                    "mensagem de indisponibilidade de voos" in motivo
                    and "pagina lenta da LATAM" not in motivo
                ):
                    _log("Interrompendo tentativas: a LATAM indicou indisponibilidade.")
                    break

            if cards:
                _log(f"OK: {len(cards)} oferta(s) encontrada(s).")

                agora = datetime.now()
                data_voo_dt = datetime.strptime(data_voo_str, "%Y-%m-%d")
                antecedencia = (data_voo_dt - agora).days

                menor_preco_info = _extrair_menor_preco_com_fallback(cards, _log)

                if menor_preco_info:
                    melhor_card = cards[menor_preco_info["card_index"]]
                    preco_num = menor_preco_info["preco"]

                    try:
                        texto_card = _texto_card(melhor_card)
                        hora_saida = _extrair_hora(
                            melhor_card,
                            SEL["hora_saida"],
                            texto_card,
                            r"hora de sa[i\u00ed]da\s+(\d{1,2}:\d{2})",
                        )
                        hora_chegada = _extrair_hora(
                            melhor_card,
                            SEL["hora_chegada"],
                            texto_card,
                            r"hora de chegada\s+(\d{1,2}:\d{2})",
                        )
                        horarios_fallback = _extrair_horarios_por_texto(texto_card)
                        if hora_saida == "N/D" and horarios_fallback:
                            hora_saida = horarios_fallback[0]
                        if hora_chegada == "N/D" and len(horarios_fallback) > 1:
                            hora_chegada = horarios_fallback[1]

                        melhor_oferta = _montar_registro(
                            plataforma="LATAM",
                            url=url,
                            origem=origem,
                            destino=destino,
                            data_voo_str=data_voo_str,
                            antecedencia=antecedencia,
                            preco_num=preco_num,
                            hora_saida=hora_saida,
                            hora_chegada=hora_chegada,
                            modo_privacidade=modo_privacidade,
                            user_agent_label=user_agent_label,
                            agora=agora,
                            experimento_id=experimento_id,
                            captcha_detectado=False
                        )

                        resultados.append(melhor_oferta)
                        _log(
                            f"OK: LATAM coleta OK: {origem}->{destino} | "
                            f"R$ {preco_num:.2f} | "
                            f"{menor_preco_info['total_precos']} voos analisados"
                        )
                    except Exception as e:
                        _log(f"Erro ao processar melhor oferta: {e}")
                else:
                    snap, motivo = _diagnosticar_estado_pagina(driver)
                    _log(
                        f"Aviso: nenhum preço válido encontrado para "
                        f"{origem}->{destino} em {data_voo_str}. "
                        f"Diagnóstico: {motivo} | titulo='{snap['titulo']}' | "
                        f"html={snap['html_len']} bytes"
                    )
            else:
                snap, motivo = _diagnosticar_estado_pagina(driver)
                _log(
                    f"Nenhum card de voo encontrado para {origem}->{destino}. "
                    f"Diagnóstico final: {motivo} | titulo='{snap['titulo']}' | "
                    f"html={snap['html_len']} bytes"
                )

    except (InvalidSessionIdException, NoSuchWindowException) as e:
        try:
            driver._sessao_invalida_tcc = True
        except Exception:
            pass
        _log(
            "Sessao do Chrome foi encerrada durante a coleta. "
            "A rota sera tentada novamente com um navegador novo."
        )
        _log(f"Motivo técnico resumido: {e.__class__.__name__}")
    except WebDriverException as e:
        mensagem = str(e).lower()
        if (
            "invalid session id" in mensagem
            or "target window already closed" in mensagem
            or "not connected to devtools" in mensagem
            or "web view not found" in mensagem
        ):
            try:
                driver._sessao_invalida_tcc = True
            except Exception:
                pass
            _log(
                "Chrome perdeu a conexão com o Selenium durante a coleta. "
                "A rota sera tentada novamente com um navegador novo."
            )
            _log(f"Motivo técnico resumido: {e.__class__.__name__}")
        else:
            _log(f"Erro geral do Selenium na coleta: {e}")
    except ColetaCancelada:
        _log("Coleta cancelada pelo usuario.")
    except Exception as e:
        _log(f"Erro geral na coleta: {e}")
        import traceback
        _log(f"Traceback: {traceback.format_exc()}")

    if captcha and not resultados:
        _log("Coleta bloqueada por CAPTCHA; nenhum registro vazio sera salvo.")

    _log(
        f"Coleta concluida: {len(resultados)} registro(s) | "
        f"{round((time.time() - t_inicio) * 1000)}ms"
    )
    return resultados