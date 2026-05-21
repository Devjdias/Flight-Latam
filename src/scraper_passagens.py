import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk, filedialog
from tkcalendar import DateEntry
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
import pandas as pd
import os
import time
import sys
import threading
import random
import gzip
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials
import warnings
import configparser

# Importa utilitários compartilhados
from scrapers_utils import destruir_html_log, hash_gz_file, _montar_registro, sleep_interrompivel

warnings.simplefilter("ignore", UserWarning)

# ─── CAMINHOS GLOBAIS ──────────────────────────────────────────────────────────

PASTA_BASE           = Path(__file__).parent.parent 
PASTA_DADOS_BRUTOS   = PASTA_BASE / "DADOS_BRUTOS"
PASTA_LOGS           = PASTA_BASE / "LOGS"
PASTA_CHROME_PROFILES = PASTA_LOGS / "chrome_profiles"
CONFIG_FILE          = PASTA_BASE / "config" / "config.ini"

for _pasta in [PASTA_DADOS_BRUTOS, PASTA_LOGS, PASTA_CHROME_PROFILES]:
    _pasta.mkdir(parents=True, exist_ok=True)

# Garante que o Python encontre os módulos (scrapers) na mesma pasta deste script
SRC_DIR = Path(__file__).parent  
if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))


# ─── CONFIGURAÇÕES DO EXPERIMENTO ─────────────────────────────────────────────

ROTAS_DISPONIVEIS = {
    "BSB -> GIG ": ("BSB", "GIG"), #(Brasília - Rio de Janeiro)
    "PVH -> BSB ":    ("PVH", "BSB"), #(Porto Velho - Brasília)
    "BSB -> FLN ":  ("BSB", "FLN"), #(Brasília - Florianópolis)
    "JPA -> BSB ":    ("JPA", "BSB"), #(João Pessoa - Brasília)
}

USER_AGENTS = {
    "Chrome Desktop (padrao)":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36",
    "Chrome Normal":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36",
}

MODOS_PRIVACIDADE = [
    "Sem cookies (perfil limpo)",
    "Normal (com cookies)",
    "Incognito",
]

COLUNAS_DATASET = [
    "id_coleta", "data_hora_local", "janela_horario",
    "plataforma", "url_consultada", "origem", "destino", "data_voo",
    "dias_antecedencia", "preco_brl", "hora_saida", "hora_chegada",
    "modo_privacidade", "user_agent_label", "captcha_detectado",
    "dia_semana_busca", "experimento_id",
]

app_instance = None
stop_event   = threading.Event()


def sessao_driver_invalida(driver):
    if not driver:
        return True
    if getattr(driver, "_sessao_invalida_tcc", False):
        return True
    try:
        _ = driver.current_window_handle
        return False
    except WebDriverException:
        return True


def start_thread(func, *args):
    t = threading.Thread(target=func, args=args, daemon=True)
    t.start()
    return t


def carregar_antidetect_config(cfg):
    return {
        "humanize":   cfg.getboolean("ANTIDETECT", "humanize", fallback=True),
        "save_html":  cfg.getboolean("ANTIDETECT", "save_html", fallback=False),
        "screenshot": cfg.getboolean("ANTIDETECT", "screenshot", fallback=False),
    }


def carregar_proxy_config(cfg):
    enabled = cfg.getboolean("PROXY", "enabled", fallback=False)
    raw = cfg.get("PROXY", "list", fallback="")
    proxies = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        proxies.append(line)
    return enabled, proxies


def salvar_html_bruto(driver, plataforma, experimento_id):
    """Salva HTML compactado e retorna (path, hash_md5). Hash confirma que dado estava lá."""
    if sessao_driver_invalida(driver):
        if app_instance:
            app_instance.log_warn("HTML bruto não salvo: sessao do Chrome já estava encerrada.")
        return None, ""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"html_{plataforma}_{experimento_id}_{ts}.html.gz"
    path = PASTA_LOGS / nome
    try:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(driver.page_source or "")
        h = hash_gz_file(path)
        if app_instance:
            app_instance.log_ok(f"HTML bruto salvo: {path.name} | MD5={h}")
        return path, h
    except Exception as e:
        if app_instance:
            app_instance.log_warn(f"Falha ao salvar HTML bruto: {e}")
        return None, ""


def salvar_screenshot(driver, plataforma, experimento_id, erro_tag="erro"):
    if sessao_driver_invalida(driver):
        if app_instance:
            app_instance.log_warn("Screenshot não salvo: sessao do Chrome já estava encerrada.")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"{erro_tag}_{plataforma}_{experimento_id}_{ts}.png"
    path = PASTA_LOGS / nome
    try:
        driver.save_screenshot(str(path))
        if app_instance:
            app_instance.log_ok(f"Screenshot salvo: {path.name}")
    except Exception as e:
        if app_instance:
            app_instance.log_warn(f"Falha ao salvar screenshot: {e}")


# ─── CONFIG ────────────────────────────────────────────────────────────────────

def montar_registro_sem_dados(plataforma, origem, destino, data_voo_str,
                              modo_privacidade, user_agent_label, experimento_id):
    agora = datetime.now()
    data_voo_dt = datetime.strptime(data_voo_str, "%Y-%m-%d")
    url = (
        "https://www.latamairlines.com/br/pt/oferta-voos"
        f"?origin={origem.upper()}"
        f"&outbound={data_voo_str}T00%3A00%3A00.000Z"
        f"&destination={destino.upper()}"
        "&adt=1&chd=0&inf=0&trip=OW&cabin=Y&redemption=false&sort=RECOMMENDED"
    )
    return _montar_registro(
        plataforma=plataforma,
        url=url,
        origem=origem,
        destino=destino,
        data_voo_str=data_voo_str,
        antecedencia=(data_voo_dt - agora).days,
        preco_num=None,
        hora_saida="N/D",
        hora_chegada="N/D",
        modo_privacidade=modo_privacidade,
        user_agent_label=user_agent_label,
        agora=agora,
        experimento_id=experimento_id,
        captcha_detectado=False,
    )


def carregar_config():
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE)
    return cfg


def salvar_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        cfg.write(f)


# ─── SELENIUM ──────────────────────────────────────────────────────────────────

def criar_driver(modo_privacidade="Sem cookies (perfil limpo)", headless=False,
                 user_agent_label=None, proxy=None):
    options = webdriver.ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    perfil_temp = None

    ua = USER_AGENTS.get(user_agent_label or "") or next(iter(USER_AGENTS.values()))
    options.add_argument(f"--user-agent={ua}")

    if modo_privacidade == "Incognito":
        options.add_argument("--incognito")
    elif modo_privacidade == "Normal (com cookies)":
        pass
    else:
        perfil_temp = tempfile.mkdtemp(prefix="chrome_profile_tcc_")
        options.add_argument(f"--user-data-dir={perfil_temp}")

    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-software-rasterizer")

    if proxy:    
        options.add_argument(f"--proxy-server={proxy}")

    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(15)
    if perfil_temp:
        driver._perfil_temp_tcc = perfil_temp
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


# ─── EXECUCAO DO EXPERIMENTO ──────────────────────────────────────────────────
def executar_experimento_thread(app_ctrl, config):
    if stop_event.is_set():
        return

    rota_label      = config["rota_label"]
    origem, destino = ROTAS_DISPONIVEIS[rota_label]
    plataforma     = config["plataforma"]
    data_voo_str    = config["data_voo_str"]
    ua_label        = config["user_agent_label"]
    modo_priv       = config["modo_privacidade"]
    exp_id          = config["experimento_id"]
    repeticoes      = int(config.get("repeticoes", 3))
    headless        = config.get("headless", False)

    cfg = carregar_config()
    antidetect = carregar_antidetect_config(cfg)
    proxy_enabled, proxy_list = carregar_proxy_config(cfg)
    proxy = None
    if proxy_enabled:
        if proxy_list:
            proxy = random.choice(proxy_list)
            app_ctrl.log_info(f"Proxy selecionado: {proxy}")
        else:
            app_ctrl.log_warn("Proxy habilitado, mas lista vazia.")

    app_ctrl.log("-" * 55, "neutro")
    app_ctrl.log_info(f"EXPERIMENTO {exp_id}  |  {rota_label}")
    app_ctrl.log_info(
        f"Plataforma: {plataforma}  |  Data voo: {data_voo_str}  |  Reps: {repeticoes}")
    app_ctrl.log_info(f"UA: {ua_label}  |  Privacidade: {modo_priv}")
    app_ctrl.log("-" * 55, "neutro")

    todos_resultados = []
    html_logs = []  

    for rep in range(1, repeticoes + 1):
        if stop_event.is_set():
            app_ctrl.log_warn("Experimento interrompido pelo usuario.")
            break

        app_ctrl.log_info(f"Repeticao {rep}/{repeticoes}...")
        resultados = []
        max_tentativas_driver = 2

        for tentativa_driver in range(1, max_tentativas_driver + 1):
            driver = None
            scraper_kwargs = None

            try:
                if tentativa_driver > 1:
                    app_ctrl.log_warn(
                        "Reiniciando navegador para repetir a mesma rota "
                        f"(tentativa {tentativa_driver}/{max_tentativas_driver})."
                    )

                modo_priv_driver = modo_priv
                if (
                    modo_priv == "Sem cookies (perfil limpo)"
                    and tentativa_driver > 1
                ):
                    modo_priv_driver = "Incognito"
                    app_ctrl.log_warn(
                        "Perfil limpo nao retornou preco; tentando fallback em "
                        "Incognito, que era o comportamento anterior desse modo."
                    )

                driver = criar_driver(
                    modo_privacidade=modo_priv_driver,
                    headless=headless,
                    user_agent_label=ua_label,
                    proxy=proxy,
                )
                try:
                    app_ctrl.current_driver = driver
                except Exception:
                    pass

                scraper_kwargs = dict(
                    driver=driver, origem=origem, destino=destino,
                    data_voo_str=data_voo_str,
                    experimento_id=f"{exp_id}_R{rep}_T{tentativa_driver}",
                    user_agent_label=ua_label,
                    modo_privacidade=modo_priv_driver,
                    log_fn=app_ctrl.log_info,
                    humanize=antidetect["humanize"],
                    should_stop=stop_event.is_set,
                )

                if plataforma == "LATAM":
                    try:
                        from latam_scraper import scrape_latam
                        resultados = scrape_latam(**scraper_kwargs)
                    except ImportError as e:
                        app_ctrl.log_err(f"latam_scraper.py nao encontrado: {e}")
                        resultados = []
                else:
                    app_ctrl.log_warn(f"Plataforma '{plataforma}' desconhecida.")
                    resultados = []

                if not resultados and antidetect["screenshot"]:
                    salvar_screenshot(
                        driver,
                        plataforma,
                        scraper_kwargs["experimento_id"],
                        "sem_dados",
                    )

                if antidetect["save_html"]:
                    path_h, hash_h = salvar_html_bruto(
                        driver,
                        plataforma,
                        scraper_kwargs["experimento_id"],
                    )
                    if path_h:
                        html_logs.append((path_h, hash_h))

                if resultados:
                    break

                if sessao_driver_invalida(driver):
                    app_ctrl.log_warn("Sessao do Chrome caiu; sera feita nova tentativa.")
                elif tentativa_driver < max_tentativas_driver:
                    app_ctrl.log_warn(
                        "Nenhum preco retornou nesta tentativa; sera feita nova "
                        "tentativa para a mesma rota."
                    )

            except Exception as e:
                app_ctrl.log_err(f"Erro na repeticao {rep}: {e}")
                if antidetect["screenshot"] and not sessao_driver_invalida(driver):
                    salvar_screenshot(driver, plataforma, f"{exp_id}_R{rep}", "erro")
            finally:
                if driver:
                    try:
                        if getattr(app_ctrl, "current_driver", None) is driver:
                            app_ctrl.current_driver = None
                    except Exception:
                        pass
                    perfil_temp = getattr(driver, "_perfil_temp_tcc", None)
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    if perfil_temp:
                        shutil.rmtree(perfil_temp, ignore_errors=True)

        todos_resultados.extend(resultados)
        app_ctrl.log_ok(f"Rep {rep}: {len(resultados)} registro(s) coletado(s).")

        if rep < repeticoes and not stop_event.is_set():
            espera = random.uniform(8, 20)
            app_ctrl.log_info(f"Aguardando {espera:.1f}s antes da proxima repeticao...")
            sleep_interrompivel(espera, espera, enabled=antidetect["humanize"], should_stop=stop_event.is_set)

    if not todos_resultados and config.get("registrar_sem_dados", False):
        todos_resultados.append(
            montar_registro_sem_dados(
                plataforma,
                origem,
                destino,
                data_voo_str,
                modo_priv,
                ua_label,
                exp_id,
            )
        )
        app_ctrl.log_warn(
            "Nenhum preco encontrado; registrando linha sem dados para manter "
            "a combinacao rota/janela no dataset."
        )

    if todos_resultados:
        salvar_e_enviar(app_ctrl, todos_resultados, exp_id, html_logs=html_logs)
    else:
        app_ctrl.log_warn("Nenhum dado coletado neste experimento.")
        limpar_html_logs_com_hash(app_ctrl, html_logs)

    app_ctrl.log_ok(
        f"Experimento {exp_id} concluido. Total: {len(todos_resultados)} registro(s).")
    if config.get("show_dialogs", True):
        app_ctrl.info("Experimento Concluido",
                      f"Experimento {exp_id} finalizado.\n"
                      f"{len(todos_resultados)} registros coletados.")
    return todos_resultados


# ─── AGENDADOR ────────────────────────────────────────────────────────────────
def executar_plataformas_sequencial(app_ctrl, plataformas, base_config):
    resultados_plataformas = []

    for plat in plataformas:

        if stop_event.is_set():
            break

        config = {
            **base_config,
            "plataforma": plat,
            "experimento_id": f"{base_config['experimento_id']}_{plat}",
        }

        try:
            resultados = executar_experimento_thread(app_ctrl, config)
            if resultados:
                resultados_plataformas.extend(resultados)
        except Exception as e:
            rota_label = config.get("rota_label", "rota desconhecida")
            app_ctrl.log_err(
                f"Erro inesperado em {rota_label} / {plat}: {e}. "
                "Agendador vai continuar para a proxima rota."
            )
    return resultados_plataformas


def executar_rotas_sequencial(app_ctrl, rotas, plataformas, base_config):
    total_rotas = len(rotas)
    resultados_rotas = []

    for idx, rota_label in enumerate(rotas, 1):
        if stop_event.is_set():
            app_ctrl.log_warn(
                f"Agendador interrompido. {idx - 1}/{total_rotas} rota(s) processada(s)."
            )
            break

        config_rota = {
            **base_config,
            "rota_label": rota_label,
            "experimento_id": f"{base_config['experimento_id']}_ROTA{idx}",
        }

        app_ctrl.log_info(f"\nAgendador - Rota {idx}/{total_rotas}: {rota_label}")
        resultados = executar_plataformas_sequencial(app_ctrl, plataformas, config_rota)
        if resultados:
            resultados_rotas.extend(resultados)

        if idx < total_rotas and not stop_event.is_set():
            espera = random.uniform(10, 20)
            app_ctrl.log_info(f"Agendador - aguardando {espera:.1f}s antes da proxima rota...")
            sleep_interrompivel(espera, espera, enabled=True, should_stop=stop_event.is_set)
    return resultados_rotas

def executar_scheduler_thread(app_ctrl, config):
    try:
        stop_event.clear()
        plataformas = config["plataformas"]
        rotas_config = config.get("rotas") or list(ROTAS_DISPONIVEIS.keys())
        rotas = [rota for rota in rotas_config if rota in ROTAS_DISPONIVEIS]
        intervalo = int(config.get("intervalo_min", 60))
        jitter    = int(config.get("jitter_min", 8))
        ciclo     = 0

        if not rotas:
            app_ctrl.log_err("Agendador nao iniciou: nenhuma rota valida selecionada.")
            app_ctrl.set_scheduler_status(False)
            return

        app_ctrl.log_ok(
            f"Agendador iniciado: {len(rotas)} rota(s), 1 repeticao por rota, "
            f"{len(config.get('janelas_dias', [30]))} janela(s), "
            f"intervalo={intervalo}min +-{jitter}min"
        )
        app_ctrl.set_scheduler_status(True)

        janelas = config.get("janelas_dias", [30])

        while not stop_event.is_set():
            ciclo += 1
            app_ctrl.log_info(f"\n======== AGENDADOR - INICIANDO CICLO {ciclo} ========")
            
            for janela in janelas:
                if stop_event.is_set():
                    break
                    
                data_voo_dinamica = (datetime.now() + timedelta(days=janela)).strftime("%Y-%m-%d")
                
                config_ciclo = {
                    **config,
                    "data_voo_str": data_voo_dinamica,
                    "experimento_id": f"{config['experimento_id']}_C{ciclo}_{janela}D",
                    "registrar_sem_dados": True,
                }
                
                app_ctrl.log_info(f"\n➔ Pesquisando antecedencia de {janela} dias (Voo: {data_voo_dinamica})")
                executar_rotas_sequencial(app_ctrl, rotas, plataformas, config_ciclo)

            if stop_event.is_set():
                break

            espera_real = (intervalo * 60) + random.uniform(-jitter * 60, jitter * 60)
            espera_real = max(60, espera_real)
            proxima = datetime.now() + timedelta(seconds=espera_real)
            
            app_ctrl.log_info(f"\n[!] Ciclo {ciclo} finalizado. Proxima coleta as ~{proxima.strftime('%H:%M:%S')}")
            app_ctrl.frames["AgendadorPage"].set_next_run(proxima.strftime("%H:%M:%S"))

            for _ in range(int(espera_real)):
                if stop_event.is_set():
                    break
                time.sleep(1)

    except Exception as e:
        app_ctrl.log_err(f"Erro Crítico na Thread do Agendador: {str(e)}")
        import traceback
        app_ctrl.log_err(traceback.format_exc())
    finally:
        app_ctrl.log_warn("Agendador encerrado.")
        app_ctrl.set_scheduler_status(False)


# ─── SALVAR EXCEL + GOOGLE SHEETS ─────────────────────────────────────────────

def salvar_excel(df, exp_id):
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = PASTA_DADOS_BRUTOS / f"passagens_{exp_id}_{ts}.xlsx"
    df.to_excel(nome, index=False, engine="openpyxl")
    if app_instance:
        app_instance.log_ok(f"Excel salvo: {nome.name}")
    return nome


def limpar_html_logs_com_hash(app_ctrl, html_logs):
    """Apaga HTMLs brutos somente depois de recalcular e conferir o MD5."""
    if not html_logs:
        return

    app_ctrl.log_info(
        f"[LOG] Verificando hash de {len(html_logs)} HTML log(s) antes de apagar..."
    )
    destruidos = 0
    for path_h, hash_h in html_logs:
        if destruir_html_log(path_h, hash_esperado=hash_h, log_fn=app_ctrl.log_ok):
            destruidos += 1

    app_ctrl.log_ok(
        f"[LOG] {destruidos}/{len(html_logs)} HTML log(s) apagado(s) "
        "com hash confirmado."
    )


def enviar_google_sheets(app_ctrl, df, arquivo_credenciais, id_planilha, nome_aba="Dados"):
    if app_ctrl is None:
        class _ConsoleAppCtrl:
            def log_info(self, msg): print(msg)
            def log_ok(self, msg): print(msg)
            def log_err(self, msg): print(msg)
            def error(self, title, msg): print(f"{title}: {msg}")

        app_ctrl = _ConsoleAppCtrl()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if not Path(arquivo_credenciais).exists():
        app_ctrl.log_err(f"[GSheets] Credencial nao encontrada: {arquivo_credenciais}")
        app_ctrl.error(
            "Google Sheets",
            f"Arquivo de credenciais nao encontrado:\n{arquivo_credenciais}",
        )
        return False

    try:
        creds  = Credentials.from_service_account_file(arquivo_credenciais, scopes=scopes)
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(id_planilha)

        try:
            aba = sheet.worksheet(nome_aba)
        except gspread.exceptions.WorksheetNotFound:
            aba = sheet.add_worksheet(
                title=nome_aba, rows=5000, cols=len(COLUNAS_DATASET)
            )
            app_ctrl.log_info(f"[GSheets] Aba '{nome_aba}' criada.")

        # Prepara o dataframe para envio
        df_envio = df.copy()
        for col in df_envio.select_dtypes(
            include=["datetime64[ns]", "datetime"]
        ).columns:
            df_envio[col] = df_envio[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        df_envio = df_envio.fillna("")

        novas_linhas = df_envio.values.tolist()

        # ── LÓGICA DE APPEND ──────────────────────────────────────────────────
        dados_existentes = aba.get_all_values()

        if not dados_existentes:
            aba.update(
                range_name="A1",
                values=[df_envio.columns.tolist()] + novas_linhas,
            )
            app_ctrl.log_ok(
                f"[GSheets] Aba '{nome_aba}' inicializada com "
                f"{len(novas_linhas)} linha(s) + cabeçalho."
            )
        else:
            proxima_linha = len(dados_existentes) + 1
            aba.update(
                range_name=f"A{proxima_linha}",
                values=novas_linhas,
            )
            total = proxima_linha - 1 + len(novas_linhas)
            app_ctrl.log_ok(
                f"[GSheets] +{len(novas_linhas)} linha(s) adicionada(s) "
                f"(linha {proxima_linha} em diante | "
                f"total acumulado: {total} linhas)."
            )
        # ─────────────────────────────────────────────────────────────────────

        return True

    except gspread.exceptions.SpreadsheetNotFound:
        app_ctrl.log_err(f"[GSheets] Planilha nao encontrada: {id_planilha}")
        app_ctrl.error(
            "Google Sheets",
            "Planilha nao encontrada. Verifique o ID e o compartilhamento.",
        )
        return False
    except Exception as e:
        app_ctrl.log_err(f"[GSheets] Erro: {e}")
        app_ctrl.error("Google Sheets", f"Erro ao enviar dados:\n{e}")
        return False


def normalizar_colunas_dataset(df):
    df = df.copy()
    for col in COLUNAS_DATASET:
        if col not in df.columns:
            df[col] = None
    return df[COLUNAS_DATASET]


def salvar_e_enviar(app_ctrl, resultados, exp_id, html_logs=None):
    """
    Salva Excel, envia ao Google Sheets e — após confirmação de envio —
    destrói os arquivos HTML de log (verificando hash MD5 antes de apagar).
    """
    df = pd.DataFrame(resultados)
    df = normalizar_colunas_dataset(df)

    salvar_excel(df, exp_id)

    cfg   = carregar_config()
    cred  = cfg.get("GSHEETS", "arquivo_credenciais", fallback="")
    id_pl = cfg.get("GSHEETS", "id_planilha",         fallback="")
    aba   = cfg.get("GSHEETS", "nome_aba",             fallback="Flight Data ETL")

    if cred and id_pl:
        app_ctrl.log_info("[GSheets] Enviando ao Google Sheets...")
        ok = enviar_google_sheets(app_ctrl, df, cred, id_pl, aba)
        if ok:
            app_ctrl.info("Google Sheets", "Dados enviados ao Google Sheets com sucesso!")
            # ── Destruição segura dos HTML logs após envio confirmado ──────────
            if html_logs:
                app_ctrl.log_info(
                    f"[LOG] Destruindo {len(html_logs)} arquivo(s) HTML log "
                    f"(hash verificado antes de apagar)..."
                )
                destruidos = 0
                for path_h, hash_h in html_logs:
                    if destruir_html_log(path_h, hash_esperado=hash_h,
                                         log_fn=app_ctrl.log_ok):
                        destruidos += 1
                app_ctrl.log_ok(
                    f"[LOG] {destruidos}/{len(html_logs)} HTML log(s) destruído(s) "
                    f"com integridade confirmada."
                )
            # ─────────────────────────────────────────────────────────────────
    else:
        app_ctrl.log_warn(
            "[GSheets] Credenciais nao configuradas. Pulando envio automatico."
        )

    limpar_html_logs_com_hash(app_ctrl, html_logs)


def enviar_arquivo_sheets_thread(app_ctrl, caminho_xlsx):
    cfg   = carregar_config()
    cred  = cfg.get("GSHEETS", "arquivo_credenciais", fallback="")
    id_pl = cfg.get("GSHEETS", "id_planilha",         fallback="")
    aba   = cfg.get("GSHEETS", "nome_aba",             fallback="Dados")

    if not cred or not id_pl:
        app_ctrl.warn("Google Sheets", "Configure as credenciais antes de enviar.")
        return

    try:
        df = pd.read_excel(caminho_xlsx)
        df = normalizar_colunas_dataset(df)
        app_ctrl.log_info(f"[GSheets] Enviando {Path(caminho_xlsx).name}...")
        enviar_google_sheets(app_ctrl, df, cred, id_pl, aba)
    except Exception as e:
        app_ctrl.log_err(f"[GSheets] Erro ao carregar arquivo: {e}")


# ─── GUI ───────────────────────────────────────────────────────────────────────

class ScraperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Scraper de Passagens Aereas")
        self.geometry("900x800")
        self.minsize(720, 620)
        self.resizable(True, True)
        self.configure(bg="#F0F4F8")

        self.stop_event        = stop_event
        self.scheduler_running = False
        self.current_driver    = None

        global app_instance
        app_instance = self

        self._setup_styles()

        container = ttk.Frame(self)
        container.pack(side="top", fill="both", expand=True, padx=15, pady=10)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for PageClass in (
            StartPage, ExperimentoPage, AgendadorPage, HistoricoPage, ConfigPage
        ):
            name  = PageClass.__name__
            frame = PageClass(parent=container, controller=self)
            self.frames[name] = frame
            frame.grid(row=0, column=0, sticky="nsew")

       
        log_frame = ttk.LabelFrame(self, text="Log de Execucao", padding="8 4 8 4")
        log_frame.pack(side="bottom", fill="x", padx=50, pady=(0, 0))
        
        
        log_frame.configure(height=600)
        log_frame.pack_propagate(False)

        self.log_widget = scrolledtext.ScrolledText(
            log_frame, height=14, wrap=tk.WORD, state="disabled",
            font=("Consolas", 9), bg="#0D1117", fg="#58D68D",
            insertbackground="white", relief=tk.FLAT, bd=0,
        )
        self.log_widget.pack(fill="both", expand=True, padx=2, pady=2)

        self.log_widget.tag_config("ok",     foreground="#58D68D")
        self.log_widget.tag_config("erro",   foreground="#E74C3C")
        self.log_widget.tag_config("aviso",  foreground="#F39C12")
        self.log_widget.tag_config("info",   foreground="#5DADE2")
        self.log_widget.tag_config("neutro", foreground="#BDC3C7")

        self.show_frame("StartPage")
        self.log_ok("Sistema iniciado. Bem-vindo ao Scraper de Passagens Aereas!")
        self.log_info("Configure os parametros e inicie a coleta ou o agendador.")

    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use("alt")

        s.configure("TFrame",      background="#F0F4F8")
        s.configure("TLabelframe", background="#F0F4F8")
        s.configure("TLabelframe.Label",
                    font=("Segoe UI", 10, "bold"), foreground="#1A3A5C")
        s.configure("TLabel",
                    background="#F0F4F8", font=("Segoe UI", 10), foreground="#2C3E50")
        s.configure("TEntry",       font=("Segoe UI", 10), fieldbackground="white")
        s.configure("TCombobox",    font=("Segoe UI", 10))
        s.configure("TCheckbutton", background="#F0F4F8", font=("Segoe UI", 10))
        s.configure("TSpinbox",     font=("Segoe UI", 10))

        btn_defs = {
            "Green":  ("#27AE60", "#1E8449"),
            "Blue":   ("#2980B9", "#1F618D"),
            "Orange": ("#E67E22", "#CA6F1E"),
            "Red":    ("#E74C3C", "#CB4335"),
            "Purple": ("#8E44AD", "#76448A"),
            "Teal":   ("#16A085", "#117A65"),
            "Gray":   ("#7F8C8D", "#626567"),
        }
        for name, (bg, active) in btn_defs.items():
            key = f"C.{name}.TButton"
            s.configure(key, background=bg, foreground="white",
                        font=("Segoe UI", 10, "bold"), borderwidth=0,
                        relief="flat", padding=(10, 6))
            s.map(key,
                  background=[("active", active), ("disabled", "#BDC3C7")],
                  foreground=[("disabled", "#ECF0F1")])

    def show_frame(self, name):
        self.frames[name].tkraise()

    def log(self, message, tag="neutro"):
        def _insert():
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_widget.configure(state="normal")
            self.log_widget.insert(tk.END, f"[{ts}] {message}\n", tag)
            self.log_widget.see(tk.END)
            self.log_widget.configure(state="disabled")
        self.after(0, _insert)

    def log_ok(self,   msg): self.log(f"[OK]   {msg}", "ok")
    def log_err(self,  msg): self.log(f"[ERR]  {msg}", "erro")
    def log_warn(self, msg): self.log(f"[WARN] {msg}", "aviso")
    def log_info(self, msg): self.log(f"[INFO] {msg}", "info")

    def info(self,  title, msg): self.after(0, lambda: messagebox.showinfo(title, msg))
    def warn(self,  title, msg): self.after(0, lambda: messagebox.showwarning(title, msg))
    def error(self, title, msg): self.after(0, lambda: messagebox.showerror(title, msg))

    def log_message_gui(self, msg):          self.log_info(msg)
    def show_info_messagebox(self, t, m):    self.info(t, m)
    def show_warning_messagebox(self, t, m): self.warn(t, m)
    def show_error_messagebox(self, t, m):   self.error(t, m)

    def request_stop(self, mensagem="Sinal de parada enviado."):
        self.stop_event.set()
        self.log_warn(mensagem)
        driver = self.current_driver
        if driver:
            start_thread(self._encerrar_driver_ativo, driver)

    def _encerrar_driver_ativo(self, driver):
        try:
            driver.quit()
        except Exception:
            pass

    def set_scheduler_status(self, running):
        self.scheduler_running = running
        self.after(0, lambda: [
            self.frames["AgendadorPage"].update_status_badge(running),
            self.frames["StartPage"].update_scheduler_badge(running),
        ])


# ─── PAGINA INICIAL ───────────────────────────────────────────────────────────

class StartPage(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        ttk.Label(self,
                  text="AirPrice Scraper",
                  font=("Segoe UI", 20, "bold"),
                  foreground="#1A3A5C").pack(pady=(28, 4))
        ttk.Label(self,
                  text="Criado por Anonymous",
                  font=("Segoe UI", 11, "italic"),
                  foreground="#6A7879").pack(pady=(0, 22))

        nav = ttk.LabelFrame(self, text="Modulos do Sistema", padding="18 12 18 12")
        nav.pack(padx=50, fill="x")

        buttons = [
            ("Coleta Manual",
             "ExperimentoPage", "C.Green.TButton",
             "Executa coleta para rota, data e parametros especificos"),
            ("Agendador Automatico",
             "AgendadorPage",   "C.Blue.TButton",
             "Coleta continua em alta frequencia (horaria ou mais)"),
            ("Historico & Exportacao",
             "HistoricoPage",   "C.Teal.TButton",
             "Visualiza arquivos gerados e envia ao Google Sheets"),
            ("Configuracoes",
             "ConfigPage",      "C.Gray.TButton",
             "Google Sheets, proxies, anti-deteccao e preferencias"),
        ]

        for label, page, style, hint in buttons:
            row = ttk.Frame(nav)
            row.pack(fill="x", pady=5)
            ttk.Button(row, text=label, style=style, width=28,
                       command=lambda p=page: controller.show_frame(p)
                       ).pack(side="left", ipady=5)
            ttk.Label(row, text=hint,
                      font=("Segoe UI", 9),
                      foreground="#475353").pack(side="left", padx=14)

        badge_row = ttk.Frame(self)
        badge_row.pack(pady=18)
        ttk.Label(badge_row, text="Agendador:",
                  font=("Segoe UI", 10)).pack(side="left")
        self.badge_label = ttk.Label(
            badge_row, text="  INATIVO  ",
            font=("Segoe UI", 10, "bold"),
            background="#E74C3C", foreground="white",
            relief="flat", padding=(6, 2),
        )
        self.badge_label.pack(side="left", padx=8)

        ttk.Button(self, text="Parar Processo Atual",
                   style="C.Red.TButton",
                   command=self._stop).pack(pady=(0, 10), ipadx=12, ipady=4)

    def update_scheduler_badge(self, running):
        if running:
            self.badge_label.configure(text="  ATIVO  ",   background="#27AE60")
        else:
            self.badge_label.configure(text="  INATIVO  ", background="#E74C3C")

    def _stop(self):
        self.controller.request_stop("Sinal de parada enviado a todos os processos.")


# ─── PAGINA DE COLETA MANUAL ──────────────────────────────────────────────────

class ExperimentoPage(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        ttk.Label(self, text="Coleta Manual de Passagens",
                  font=("Segoe UI", 15, "bold"), foreground="#1A3A5C").pack(pady=(20, 14))

        form = ttk.LabelFrame(
            self, text="Parâmetros do Experimento", padding="16 10 16 10"
        )
        form.pack(padx=30, fill="x")
        
        form.columnconfigure(1, weight=1)

        def lbl_entry(row, text, default):
            ttk.Label(form, text=text).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
            var = tk.StringVar(value=default)
            ttk.Entry(form, textvariable=var, font=("Segoe UI", 10)).grid(row=row, column=1, sticky="ew", pady=5)
            return var

        def lbl_combo(row, text, values, default):
            ttk.Label(form, text=text).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
            var = tk.StringVar(value=default)
            ttk.Combobox(form, textvariable=var, values=values, state="readonly").grid(row=row, column=1, sticky="ew", pady=5)
            return var

        self.exp_id_var = lbl_entry(0, "ID do Experimento:", "E1")
        
        # ─── Seleção de Rotas ───────────────────────────────
        ttk.Label(form, text="Rotas:").grid(row=1, column=0, sticky="nw", pady=5, padx=(0, 10))
        rotas_frame = ttk.Frame(form)
        rotas_frame.grid(row=1, column=1, sticky="ew", pady=5)
        
        self.rotas_vars = {}
        for i, rota in enumerate(list(ROTAS_DISPONIVEIS.keys())):
            var = tk.BooleanVar(value=True)
            self.rotas_vars[rota] = var
            ttk.Checkbutton(rotas_frame, text=rota, variable=var).pack(side="left", padx=(0, 15), pady=2)
        
        btn_todas = ttk.Button(rotas_frame, text="Todas", width=6, command=self._selecionar_todas_rotas)
        btn_todas.pack(side="left", padx=(10, 5))
        
        btn_nenhuma = ttk.Button(rotas_frame, text="Nenhuma", width=8, command=self._selecionar_nenhuma_rota)
        btn_nenhuma.pack(side="left", padx=(0, 5))

        ttk.Label(form, text="Plataforma:").grid(row=2, column=0, sticky="w", pady=5, padx=(0, 10))
        ttk.Label(form, text="LATAM (fixo)", font=("Segoe UI", 10, "bold")).grid(row=2, column=1, sticky="w", pady=5)

        # Calendário
        ttk.Label(form, text="Data do Voo:").grid(row=3, column=0, sticky="w", pady=5, padx=(0, 10))
        self.calendario = DateEntry(
            form, background="darkblue", foreground="white", borderwidth=2,
            date_pattern="dd/mm/yyyy", locale="pt_BR", mindate=datetime.now()
        )
        self.calendario.grid(row=3, column=1, sticky="ew", pady=5)

        ua_keys = list(USER_AGENTS.keys())
        ua_default = ua_keys[0] if ua_keys else ""
        self.ua_var   = lbl_combo(4, "User-Agent:", ua_keys, ua_default)
        self.priv_var = lbl_combo(5, "Modo de Privacidade:", MODOS_PRIVACIDADE, "Sem cookies (perfil limpo)")

        ttk.Label(form, text="Repetições (min. 1):").grid(row=6, column=0, sticky="w", pady=5, padx=(0, 10))
        self.rep_var = tk.IntVar(value=3)
        ttk.Spinbox(form, from_=3, to=10, textvariable=self.rep_var, font=("Segoe UI", 10)).grid(row=6, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="Modo Headless (sem janela):").grid(row=7, column=0, sticky="w", pady=5, padx=(0, 10))
        self.headless_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, variable=self.headless_var).grid(row=7, column=1, sticky="w", pady=5)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=16, padx=30, fill="x")
        ttk.Button(btn_row, text="Iniciar Coleta", style="C.Green.TButton", command=self._iniciar).pack(side="left", expand=True, fill="x", padx=(0, 6), ipady=5)
        ttk.Button(btn_row, text="Parar", style="C.Red.TButton", command=lambda: self.controller.request_stop("Sinal de paragem enviado.")).pack(side="left", expand=True, fill="x", padx=(6, 0), ipady=5)
        ttk.Button(self, text="<- Voltar ao Início", style="C.Gray.TButton", command=lambda: controller.show_frame("StartPage")).pack(pady=(0, 10))

    def _selecionar_todas_rotas(self):
        for var in self.rotas_vars.values():
            var.set(True)

    def _selecionar_nenhuma_rota(self):
        for var in self.rotas_vars.values():
            var.set(False)

    def _iniciar(self):
        try:
            data_voo = self.calendario.get_date().strftime("%Y-%m-%d")
        except ValueError:
            self.controller.error("Data inválida", "Use o calendário para selecionar a data.")
            return

        rotas_selecionadas = [rota for rota, var in self.rotas_vars.items() if var.get()]
        if not rotas_selecionadas:
            self.controller.error("Nenhuma rota selecionada", "Selecione pelo menos uma rota para coletar dados.")
            return

        plataformas_selecionadas = ["LATAM"]
        self.controller.stop_event.clear()

        def job_sequencial():
            total_registros = 0
            rotas_com_dados = 0
            for idx, rota_label in enumerate(rotas_selecionadas, 1):
                if stop_event.is_set():
                    self.controller.log_warn(f"Coleta interrompida pelo utilizador. {idx}/{len(rotas_selecionadas)} rotas processadas.")
                    break
                base_config = {
                    "rota_label": rota_label, "data_voo_str": data_voo,
                    "modo_privacidade": self.priv_var.get(), "user_agent_label": self.ua_var.get(),
                    "experimento_id": f"{self.exp_id_var.get().strip()}_R{idx}",
                    "repeticoes": self.rep_var.get(), "headless": self.headless_var.get(),
                    "show_dialogs": False,
                }
                self.controller.log_info(f"\n━━━ ROTA {idx}/{len(rotas_selecionadas)}: {rota_label} ━━━")
                resultados = executar_plataformas_sequencial(self.controller, plataformas_selecionadas, base_config)
                total_registros += len(resultados)
                if resultados:
                    rotas_com_dados += 1
                if idx < len(rotas_selecionadas) and not stop_event.is_set():
                    espera = random.uniform(10, 20)
                    self.controller.log_info(f"A aguardar {espera:.1f}s antes da próxima rota...")
                    sleep_interrompivel(espera, espera, enabled=True, should_stop=stop_event.is_set)
            
            if not stop_event.is_set():
                resumo = f"Coleta concluída: {rotas_com_dados}/{len(rotas_selecionadas)} rota(s) com dados, {total_registros} registo(s) salvo(s)."
                self.controller.log_ok(f"\n{resumo}")
                self.controller.info("Coleta Completa", resumo)

        start_thread(job_sequencial)


# ─── PAGINA DO AGENDADOR ──────────────────────────────────────────────────────

class AgendadorPage(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        ttk.Label(self, text="Agendador de Alta Frequência",
                  font=("Segoe UI", 15, "bold"), foreground="#1A3A5C").pack(pady=(20, 14))

        badge_row = ttk.Frame(self)
        badge_row.pack(pady=(0, 10))
        ttk.Label(badge_row, text="Status:", font=("Segoe UI", 11)).pack(side="left")
        self.status_badge = ttk.Label(
            badge_row, text="  INATIVO  ", font=("Segoe UI", 11, "bold"),
            background="#E74C3C", foreground="white", relief="flat", padding=(8, 3)
        )
        self.status_badge.pack(side="left", padx=10)

        cfg_frame = ttk.LabelFrame(
            self, text="Configurações do Agendador", padding="16 10 16 10"
        )
        cfg_frame.pack(padx=30, fill="x")
        
        cfg_frame.columnconfigure(1, weight=1)
        cfg_frame.columnconfigure(3, weight=1)

        ttk.Label(cfg_frame, text="Preset de frequência:").grid(row=0, column=0, sticky="w", pady=6, padx=(0, 10))
        self.preset_var = tk.StringVar(value="media (1h)")
        preset_cb = ttk.Combobox(
            cfg_frame, textvariable=self.preset_var,
            values=["baixa (3h)", "media (1h)", "alta (30min)", "maxima (15min)"],
            state="readonly"
        )
        preset_cb.grid(row=0, column=1, columnspan=3, sticky="ew", pady=6)
        preset_cb.bind("<<ComboboxSelected>>", self._apply_preset)

        ttk.Label(cfg_frame, text="Intervalo (min):").grid(row=1, column=0, sticky="w", pady=6, padx=(0, 10))
        self.interval_var = tk.IntVar(value=60)
        ttk.Spinbox(cfg_frame, from_=5, to=360, textvariable=self.interval_var, font=("Segoe UI", 10)).grid(row=1, column=1, sticky="ew", pady=6)

        ttk.Label(cfg_frame, text="Jitter +-(min):").grid(row=1, column=2, sticky="w", pady=6, padx=(20, 10))
        self.jitter_var = tk.IntVar(value=8)
        ttk.Spinbox(cfg_frame, from_=0, to=30, textvariable=self.jitter_var, font=("Segoe UI", 10)).grid(row=1, column=3, sticky="ew", pady=6)

        ttk.Label(cfg_frame, text="Rotas:").grid(row=2, column=0, sticky="w", pady=6, padx=(0, 10))
        rotas_frame = ttk.Frame(cfg_frame)
        rotas_frame.grid(row=2, column=1, columnspan=3, sticky="ew", pady=6)

        self.scheduler_rotas_vars = {}
        for rota in list(ROTAS_DISPONIVEIS.keys()):
            var = tk.BooleanVar(value=True)
            self.scheduler_rotas_vars[rota] = var
            ttk.Checkbutton(rotas_frame, text=rota, variable=var).pack(side="left", padx=(0, 12), pady=2)

        ttk.Button(rotas_frame, text="Todas", width=6, command=self._selecionar_todas_rotas_scheduler).pack(side="left", padx=(8, 5))
        ttk.Button(rotas_frame, text="Nenhuma", width=8, command=self._selecionar_nenhuma_rota_scheduler).pack(side="left", padx=(0, 5))

        ttk.Label(cfg_frame, text="Plataforma:").grid(row=3, column=0, sticky="w", pady=6, padx=(0, 10))
        ttk.Label(cfg_frame, text="LATAM (fixo)", font=("Segoe UI", 10, "bold")).grid(row=3, column=1, sticky="w", pady=6)

        ttk.Label(cfg_frame, text="User-Agent:").grid(row=4, column=0, sticky="w", pady=6, padx=(0, 10))
        self.ua_var = tk.StringVar(value="Chrome Desktop (padrao)")
        ttk.Combobox(
            cfg_frame, textvariable=self.ua_var, values=list(USER_AGENTS.keys()), state="readonly"
        ).grid(row=4, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(cfg_frame, text="Modo Privacidade:").grid(row=5, column=0, sticky="w", pady=6, padx=(0, 10))
        self.priv_var = tk.StringVar(value="Sem cookies (perfil limpo)")
        ttk.Combobox(
            cfg_frame, textvariable=self.priv_var, values=MODOS_PRIVACIDADE, state="readonly"
        ).grid(row=5, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(cfg_frame, text="Janelas (Antecedencia):").grid(row=6, column=0, sticky="w", pady=6, padx=(0, 10))
        janelas_frame = ttk.Frame(cfg_frame)
        janelas_frame.grid(row=6, column=1, columnspan=3, sticky="ew", pady=6)

        self.janelas_vars = {}
        for dias in [7, 15, 30, 90]:
            var = tk.BooleanVar(value=True)
            self.janelas_vars[dias] = var
            ttk.Checkbutton(janelas_frame, text=f"{dias} dias", variable=var).pack(side="left", padx=(0, 15), pady=2)

        ttk.Label(cfg_frame, text="Experimento ID:").grid(row=7, column=0, sticky="w", pady=6, padx=(0, 10))
        self.exp_var = tk.StringVar(value="SCHED-01")
        ttk.Entry(cfg_frame, textvariable=self.exp_var, font=("Segoe UI", 10)).grid(row=7, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(cfg_frame, text="Modo Headless (sem janela):").grid(row=8, column=0, sticky="w", pady=6, padx=(0, 10))
        self.headless_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg_frame, variable=self.headless_var).grid(row=8, column=1, sticky="w", pady=6)

        self.next_run_label = ttk.Label(self, text="", font=("Segoe UI", 9, "italic"), foreground="#7F8C8D")
        self.next_run_label.pack(pady=(8, 0))

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=14, padx=30, fill="x")

        self.start_btn = ttk.Button(btn_row, text="Iniciar Agendador", style="C.Green.TButton", command=self._start_scheduler)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 6), ipady=5)

        self.stop_btn = ttk.Button(btn_row, text="Parar Agendador", style="C.Red.TButton", command=self._stop_scheduler, state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(6, 0), ipady=5)

        ttk.Button(self, text="<- Voltar ao Início", style="C.Gray.TButton", command=lambda: controller.show_frame("StartPage")).pack(pady=(0, 10))

    def _apply_preset(self, event=None):
        presets = {
            "baixa (3h)":    (180, 15),
            "media (1h)":    (60,  8),
            "alta (30min)":  (30,  5),
            "maxima (15min)":(15,  2),
        }
        iv, jv = presets.get(self.preset_var.get(), (60, 8))
        self.interval_var.set(iv)
        self.jitter_var.set(jv)

    def _selecionar_todas_rotas_scheduler(self):
        for var in self.scheduler_rotas_vars.values():
            var.set(True)

    def _selecionar_nenhuma_rota_scheduler(self):
        for var in self.scheduler_rotas_vars.values():
            var.set(False)

    def _start_scheduler(self):
        plataformas = ["LATAM"]
        rotas_selecionadas = [rota for rota, var in self.scheduler_rotas_vars.items() if var.get()]

        if not rotas_selecionadas:
            self.controller.error("Nenhuma rota selecionada", "Selecione pelo menos uma rota para iniciar o agendador.")
            return
            
        janelas_selecionadas = [dias for dias, var in self.janelas_vars.items() if var.get()]
        if not janelas_selecionadas:
            self.controller.error("Nenhuma janela", "Selecione pelo menos uma janela de antecedência.")
            return

        config = {
            "rotas":            rotas_selecionadas,
            "plataformas":      plataformas,
            "janelas_dias":     janelas_selecionadas,
            "user_agent_label": self.ua_var.get(),
            "modo_privacidade": self.priv_var.get(),
            "experimento_id":   self.exp_var.get().strip() or "AGEND-01",
            "repeticoes":       1,
            "headless":         self.headless_var.get(),
            "intervalo_min":    self.interval_var.get(),
            "jitter_min":       self.jitter_var.get(),
            "show_dialogs":     False,
        }
        self.controller.stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        start_thread(executar_scheduler_thread, self.controller, config)

    def _stop_scheduler(self):
        self.controller.request_stop("Sinal de paragem enviado ao agendador...")
        self.stop_btn.configure(state="disabled")
        self.start_btn.configure(state="normal")

    def update_status_badge(self, running):
        if running:
            self.status_badge.configure(text="  ATIVO  ",   background="#27AE60")
        else:
            self.status_badge.configure(text="  INATIVO  ", background="#E74C3C")
            self.next_run_label.configure(text="")
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")

    def set_next_run(self, hora_str):
        self.after(0, lambda: self.next_run_label.configure(text=f"Próxima coleta prevista às {hora_str}"))

# ─── PAGINA DE HISTORICO ──────────────────────────────────────────────────────

class HistoricoPage(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        ttk.Label(self, text="Historico & Exportacao",
                  font=("Segoe UI", 15, "bold"), foreground="#1A3A5C").pack(pady=(20, 4))
        ttk.Label(self, text=f"Arquivos em: {PASTA_DADOS_BRUTOS}",
                  font=("Segoe UI", 9), foreground="#7F8C8D").pack(pady=(0, 10))

        list_frame = ttk.LabelFrame(self, text="Arquivos Excel Gerados", padding="10 8")
        list_frame.pack(padx=30, fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(
            list_frame,
            bg="white", fg="#2C3E50",
            selectbackground="#2980B9", selectforeground="white",
            font=("Consolas", 10), relief=tk.FLAT, borderwidth=1,
            activestyle="none",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.pack(fill="both", expand=True, padx=2, pady=2)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=10, fill="x", padx=30)

        ttk.Button(btn_row, text="Atualizar Lista", style="C.Blue.TButton",
                   command=self._atualizar
                   ).pack(side="left", expand=True, fill="x", padx=(0, 6), ipady=5)
        ttk.Button(btn_row, text="Enviar ao Google Sheets", style="C.Green.TButton",
                   command=self._enviar_selecionado
                   ).pack(side="left", expand=True, fill="x", padx=(6, 6), ipady=5)
        ttk.Button(
            btn_row, text="Abrir pasta", style="C.Orange.TButton",
            command=lambda: (
                os.startfile(PASTA_DADOS_BRUTOS) if os.name == "nt"
                else os.system(f'open "{PASTA_DADOS_BRUTOS}"')
            ),
        ).pack(side="left", expand=True, fill="x", padx=(6, 0), ipady=5)

        ttk.Button(self, text="<- Voltar ao Inicio", style="C.Gray.TButton",
                   command=lambda: controller.show_frame("StartPage")).pack(pady=(0, 10))

        self._atualizar()

    def _atualizar(self):
        self.listbox.delete(0, tk.END)
        arquivos = sorted(
            PASTA_DADOS_BRUTOS.glob("*.xlsx"),
            key=os.path.getmtime, reverse=True,
        )
        for f in arquivos:
            self.listbox.insert(tk.END, f.name)
        self.controller.log_info(
            f"Historico: {len(arquivos)} arquivo(s) encontrado(s)."
        )

    def _enviar_selecionado(self):
        sel = self.listbox.curselection()
        if not sel:
            self.controller.warn("Historico", "Selecione um arquivo da lista.")
            return
        nome = self.listbox.get(sel[0])
        path = str(PASTA_DADOS_BRUTOS / nome)
        self.controller.log_info(f"Enviando {nome} ao Google Sheets...")
        start_thread(enviar_arquivo_sheets_thread, self.controller, path)


# ─── PAGINA DE CONFIGURACOES ──────────────────────────────────────────────────

class ConfigPage(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        ttk.Label(self, text="Configuracoes do Experimento",
                  font=("Segoe UI", 15, "bold"), foreground="#1A3A5C").pack(pady=(20, 14))

        cfg = carregar_config()

        # Google Sheets
        gs_frame = ttk.LabelFrame(self, text="Google Sheets", padding="16 10 16 10")
        gs_frame.pack(padx=30, fill="x")
        gs_frame.columnconfigure(1, weight=1)

        def lbl_entry(parent, row, text, default):
            ttk.Label(parent, text=text).grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=5
            )
            var = tk.StringVar(value=default)
            ttk.Entry(parent, textvariable=var, font=("Segoe UI", 10)
                      ).grid(row=row, column=1, sticky="ew", pady=5)
            return var

        self.cred_var = lbl_entry(
            gs_frame, 0, "Arquivo de credenciais (.json):",
            cfg.get("GSHEETS", "arquivo_credenciais", fallback=""),
        )
        self.plan_var = lbl_entry(
            gs_frame, 1, "ID da Planilha Google Sheets:",
            cfg.get("GSHEETS", "id_planilha", fallback=""),
        )
        self.aba_var = lbl_entry(
            gs_frame, 2, "Nome da aba:",
            cfg.get("GSHEETS", "nome_aba", fallback="Dados"),
        )

        ttk.Button(gs_frame, text="Selecionar credencial (.json)",
                   style="C.Gray.TButton",
                   command=self._selecionar_cred
                   ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0), ipady=4)

        # Anti-deteccao
        ad_frame = ttk.LabelFrame(self, text="Anti-deteccao", padding="14 10 14 10")
        ad_frame.pack(padx=30, pady=10, fill="x")

        self.humanize_var   = tk.BooleanVar(
            value=cfg.getboolean("ANTIDETECT", "humanize", fallback=True)
        )
        self.save_html_var  = tk.BooleanVar(
            value=cfg.getboolean("ANTIDETECT", "save_html", fallback=True)
        )
        self.screenshot_var = tk.BooleanVar(
            value=cfg.getboolean("ANTIDETECT", "screenshot", fallback=False)
        )

        ttk.Checkbutton(ad_frame,
                        text="Delays humanizados entre requisicoes (recomendado)",
                        variable=self.humanize_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(ad_frame,
                        text="Salvar HTML bruto comprimido por coleta",
                        variable=self.save_html_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(ad_frame,
                        text="Screenshot automatico em caso de erro",
                        variable=self.screenshot_var).pack(anchor="w", pady=2)

        # Proxies
        proxy_frame = ttk.LabelFrame(
            self, text="Proxies (opcional)", padding="14 10 14 10"
        )
        proxy_frame.pack(padx=30, pady=(0, 10), fill="x")

        self.proxy_enabled = tk.BooleanVar(
            value=cfg.getboolean("PROXY", "enabled", fallback=False)
        )
        ttk.Checkbutton(proxy_frame, text="Habilitar proxies rotativos",
                        variable=self.proxy_enabled).pack(anchor="w", pady=(0, 4))

        ttk.Label(proxy_frame, text="Lista (host:porta — um por linha):",
                  font=("Segoe UI", 9)).pack(anchor="w")
        self.proxy_list = tk.Text(
            proxy_frame, height=3, font=("Consolas", 9),
            bg="white", relief=tk.FLAT, bd=1,
        )
        self.proxy_list.pack(fill="x", pady=4)
        proxy_default = cfg.get("PROXY", "list", fallback="").strip()
        if proxy_default:
            self.proxy_list.insert("1.0", proxy_default)
        else:
            self.proxy_list.insert("1.0", "# Ex: 192.168.1.1:8080\n# user:pass@host:porta")

        ttk.Button(self, text="Salvar Configuracoes",
                   style="C.Blue.TButton",
                   command=self._salvar).pack(pady=10, ipadx=10, ipady=5)

        ttk.Button(self, text="<- Voltar ao Inicio", style="C.Gray.TButton",
                   command=lambda: controller.show_frame("StartPage")).pack(pady=(0, 10))

    def _selecionar_cred(self):
        path = filedialog.askopenfilename(
            title="Selecionar credencial Google (.json)",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if path:
            self.cred_var.set(path)

    def _salvar(self):
        cfg = carregar_config()
        if not cfg.has_section("GSHEETS"):
            cfg.add_section("GSHEETS")
        cfg["GSHEETS"]["arquivo_credenciais"] = self.cred_var.get()
        cfg["GSHEETS"]["id_planilha"]         = self.plan_var.get()
        cfg["GSHEETS"]["nome_aba"]            = self.aba_var.get()

        if not cfg.has_section("ANTIDETECT"):
            cfg.add_section("ANTIDETECT")
        cfg["ANTIDETECT"]["humanize"]   = str(self.humanize_var.get())
        cfg["ANTIDETECT"]["save_html"]  = str(self.save_html_var.get())
        cfg["ANTIDETECT"]["screenshot"] = str(self.screenshot_var.get())

        if not cfg.has_section("PROXY"):
            cfg.add_section("PROXY")
        cfg["PROXY"]["enabled"] = str(self.proxy_enabled.get())
        cfg["PROXY"]["list"]    = self.proxy_list.get("1.0", tk.END).strip()

        salvar_config(cfg)
        self.controller.log_ok("Configuracoes salvas com sucesso.")
        self.controller.info("Configuracoes", "Salvo com sucesso!")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ScraperApp()
    app.mainloop()
