# Documentação Técnica e Arquitetura do Sistema

Este documento descreve a estrutura técnica, o fluxo de execução e as decisões de implementação do sistema de coleta de preços de passagens aéreas da LATAM.

## 1. Visão Geral da Arquitetura

O projeto foi construído em Python, com separação entre interface gráfica, orquestração da coleta, scraper especializado da LATAM e funções utilitárias de normalização.

- **Interface gráfica:** implementada com `tkinter`, `ttk`, `scrolledtext` e `tkcalendar`. A GUI permite executar coletas manuais, iniciar/parar o agendador, configurar Google Sheets, proxy e opções de anti-detecção, além de acompanhar o log de execução em uma caixa de texto visual.
- **Execução em segundo plano:** tarefas longas são iniciadas em `threading.Thread` por meio da função `start_thread()`, evitando que a janela principal fique travada durante a automação.
- **Backend de scraping:** implementado com `Selenium WebDriver`, usando Chrome. Selenium é necessário porque a página da LATAM é dinâmica e depende de carregamento JavaScript.
- **Persistência local:** os registros coletados são padronizados em dicionários, normalizados em um `DataFrame` do `pandas` e exportados como arquivos `.xlsx` em `DADOS_BRUTOS/` com `openpyxl`.
- **Persistência em nuvem:** quando configurado, o envio ao Google Sheets é feito com `gspread` e `google.oauth2.service_account.Credentials`, usando uma Service Account JSON.
- **Logs e evidências:** screenshots de erro e HTML bruto compactado podem ser salvos em `LOGS/`, conforme as flags do `config.ini`.

## 2. Módulos Principais

### `scraper_passagens.py`

É o módulo central do sistema. Ele contém:

- constantes de rotas (`ROTAS_DISPONIVEIS`), user-agents, modos de privacidade e colunas do dataset;
- criação e configuração do Chrome WebDriver;
- execução de experimentos, plataformas, rotas e ciclos agendados;
- salvamento em Excel;
- envio ao Google Sheets;
- limpeza segura dos HTMLs brutos com conferência de MD5;
- classes da interface gráfica.

### `latam_scraper.py`

Contém o scraper especializado da LATAM. A função principal é `scrape_latam()`.

Ela monta a URL da LATAM com origem, destino e data do voo, abre a página pelo Selenium, fecha banners de cookies quando possível, identifica bloqueios reais, procura cards de voo, extrai o menor preço válido e retorna registros no padrão do dataset.

### `scrapers_utils.py`

Reúne funções auxiliares para:

- normalizar textos de preço;
- extrair o menor preço dos cards;
- montar o registro final da coleta;
- classificar a janela de horário da busca;
- gerar IDs de coleta;
- aplicar sleeps interrompíveis;
- calcular hash MD5 de HTMLs compactados;
- apagar logs HTML com conferência de integridade.

### `coleta_tcc.py`

Script de execução acadêmica. Ele executa todas as rotas cadastradas para as janelas de antecedência `[7, 15, 30, 90]`, sempre em modo headless, com uma repetição por rota e `registrar_sem_dados=True`.

## 3. Fluxo de Execução

O fluxo principal passa pelas funções de orquestração em `scraper_passagens.py`:

1. A GUI, o agendador ou `coleta_tcc.py` monta um `base_config` com rota, data do voo, modo de privacidade, user-agent, repetição, headless e identificador do experimento.
2. `executar_rotas_sequencial()` percorre as rotas selecionadas.
3. `executar_plataformas_sequencial()` percorre as plataformas configuradas. No estado atual do projeto, a plataforma efetivamente implementada é `LATAM`.
4. `executar_experimento_thread()` cria o navegador, chama `scrape_latam()` e controla tentativas por repetição.
5. Se houver resultado, os registros são acumulados.
6. Se não houver resultado e `registrar_sem_dados=True`, o sistema cria uma linha padronizada sem preço para preservar a combinação de rota e janela no dataset.
7. Ao final, `salvar_e_enviar()` salva Excel, tenta enviar ao Google Sheets quando as credenciais estão configuradas e limpa os HTMLs brutos com validação de hash.

## 4. Configuração do Navegador

A função `criar_driver()` configura o Chrome WebDriver com:

- user-agent selecionável;
- modo `Incognito`;
- modo `Normal (com cookies)`;
- modo `Sem cookies (perfil limpo)`, usando um perfil temporário de Chrome;
- modo headless opcional com janela `1920x1080`;
- proxy opcional;
- timeout de carregamento e script;
- opções para reduzir sinais de automação, como `--disable-blink-features=AutomationControlled`, `excludeSwitches=["enable-automation"]` e redefinição de `navigator.webdriver`.

O código atual não bloqueia explicitamente o carregamento de imagens do navegador.

## 5. Estratégia de Coleta na LATAM

`scrape_latam()` monta URLs no formato:

```text
https://www.latamairlines.com/br/pt/oferta-voos?origin=ORIGEM&outbound=DATA...&destination=DESTINO...
```

Depois da abertura da página, o scraper:

- aguarda com `sleep_interrompivel()`, respeitando a opção `humanize`;
- tenta fechar banners de cookies;
- verifica indicadores de bloqueio real, como reCAPTCHA e desafios;
- procura cards por seletores CSS principais e alternativos;
- tenta recarregar a página quando não encontra cards;
- detecta tela lenta ou indisponibilidade da LATAM;
- extrai preços por seletores específicos e por fallback textual;
- escolhe o menor preço válido;
- extrai horários de saída e chegada por seletores e fallback em regex;
- monta um registro final com preço, rota, data, antecedência, horário, user-agent, modo de privacidade e `experimento_id`.

O controle de espera é feito por laços, `find_elements()` e sleeps interrompíveis.

## 6. Estrutura dos Dados

O dataset final segue as colunas definidas em `COLUNAS_DATASET`:

```text
id_coleta, data_hora_local, janela_horario, plataforma, url_consultada,
origem, destino, data_voo, dias_antecedencia, preco_brl, hora_saida,
hora_chegada, modo_privacidade, user_agent_label, captcha_detectado,
dia_semana_busca, experimento_id
```

Antes de salvar ou enviar, `normalizar_colunas_dataset()` garante que todas as colunas esperadas existam e estejam na ordem correta.

## 7. Excel e Google Sheets

`salvar_excel()` grava um arquivo `.xlsx` em `DADOS_BRUTOS/` com nome baseado no `experimento_id` e timestamp.

Quando `arquivo_credenciais` e `id_planilha` estão preenchidos em `config/config.ini`, `enviar_google_sheets()`:

1. autentica a Service Account;
2. abre a planilha pelo ID;
3. localiza ou cria a aba configurada;
4. cria cabeçalho quando a aba está vazia;
5. acrescenta novas linhas abaixo da última linha existente.

Se as credenciais não estiverem configuradas, o sistema mantém apenas o Excel local e registra aviso no log.

## 8. Evidências, HTML Bruto e Limpeza

As opções de evidência ficam em `[ANTIDETECT]` no `config.ini`:

- `screenshot=True`: salva screenshot em `LOGS/` quando uma tentativa não retorna dados ou quando ocorre erro com sessão válida.
- `save_html=True`: salva o HTML atual como `.html.gz` em `LOGS/` e calcula o MD5 do arquivo.

Após o salvamento dos dados, os HTMLs brutos listados para o experimento são apagados por `destruir_html_log()` somente depois de recalcular e comparar o MD5 esperado. Isso reduz o risco de apagar um arquivo diferente do que foi registrado.

No código atual, a rotina de limpeza de HTML é chamada ao final de `salvar_e_enviar()`. Quando o envio ao Google Sheets é bem-sucedido, há também uma limpeza imediata dentro do bloco de sucesso, seguida pela chamada geral de limpeza. Por isso o log pode mostrar uma primeira remoção bem-sucedida e, logo depois, mensagens informando que o arquivo já havia sido removido.

## 9. Agendador

O agendador roda em thread separada e executa ciclos contínuos até receber sinal de parada.

Cada ciclo:

- percorre as janelas de antecedência configuradas;
- calcula `data_voo` dinamicamente com `datetime.now() + janela`;
- executa as rotas selecionadas;
- usa `registrar_sem_dados=True`;
- aguarda o próximo ciclo com intervalo configurado e jitter aleatório.

O controle de parada usa `stop_event`, permitindo encerrar o agendador e também interromper sleeps humanizados.

## 10. Coleta

`coleta_tcc.py` executa o protocolo padronizado:

- rotas: todas as rotas cadastradas em `ROTAS_DISPONIVEIS`;
- plataforma: `LATAM`;
- janelas: `[7, 15, 30, 90]`;
- repetições: `1`;
- navegador: headless;
- modo de privacidade: `Sem cookies (perfil limpo)`;
- registro sem dados: habilitado.

O objetivo dessa rotina não é buscar a melhor data de compra, mas registrar o preço observado para uma rota em uma antecedência específica.

## 11. Limitações Atuais

- Apenas a LATAM está implementada.
- A documentação e publicação devem evitar versionar `config.ini`, credenciais JSON, `DADOS_BRUTOS/`, `LOGS/` e arquivos de cache Python.
- O HTML bruto é uma evidência transitória: ele pode ser criado, verificado por MD5 e removido no mesmo fluxo de execução.