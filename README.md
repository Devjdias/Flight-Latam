# Scraper de Passagens Aéreas

Sistema em Python para coleta automatizada de preços de passagens aéreas da LATAM, com exportação local em Excel e envio opcional para Google Sheets.

O projeto foi desenvolvido para apoiar análises acadêmicas de variação de preços de voos, especialmente em janelas de antecedência de compra. Ele pode ser usado pela interface gráfica ou pelo script de coleta padronizada.

## Funcionalidades Principais

- **Interface gráfica:** GUI em `tkinter`, `ttk`, `scrolledtext` e `tkcalendar`, com execução manual, agendador, histórico de arquivos, configuração de credenciais e log visual.
- **Coleta LATAM:** automação com `Selenium WebDriver` e Chrome.
- **Execução headless:** opção de rodar o navegador oculto em coletas automatizadas.
- **Agendador:** ciclos automáticos com rotas selecionadas, janelas de antecedência configuráveis, intervalo e jitter.
- **Coleta acadêmica:** `coleta_tcc.py` executa todas as rotas cadastradas para as janelas de 7, 15, 30 e 90 dias.
- **Exportação local:** geração de arquivos `.xlsx` em `DADOS_BRUTOS/`.
- **Google Sheets:** envio opcional dos dados por `gspread` e Service Account.
- **Evidências de execução:** opção de salvar screenshots e HTML bruto compactado em `LOGS/`.
- **Anti-detecção:** modo sem cookies com perfil temporário, modo incógnito, user-agent configurável, esperas humanizadas e proxy opcional.

## Pré-Requisitos

- Python 3.10 ou superior.
- Google Chrome instalado.
- Acesso à internet.
- Opcional: Service Account do Google Cloud para integração com Google Sheets.

## Instalação

Baixe o projeto pela página pública oficial do GitHub informada no artigo, usando `Code > Download ZIP`, ou clone o repositório:

```powershell
git clone https://github.com/Devjdias/Flight-Latam.git
cd Flight-Latam
```

No Windows, abra o terminal na pasta do projeto e execute:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r config\requirements.txt
```

No Linux/macOS, instale previamente o Google Chrome. Em distribuições Linux que não trazem `tkinter` por padrão, instale também o pacote do sistema correspondente, como `python3-tk` no Ubuntu/Debian. Depois execute:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r config/requirements.txt
```

## Fonte Oficial e Pacote Suplementar

O repositório GitHub informado no artigo de apresentação deve ser tratado como a página pública oficial do projeto. Ele deve conter o código-fonte, a documentação, a licença, as instruções de instalação, o arquivo `config/config.example.ini` e os materiais complementares públicos mantidos em `data/`.

Para submissão suplementar, gere um ZIP limpo a partir da raiz do projeto:

```powershell
.\scripts\build_submission_zip.ps1
```

O arquivo será criado em:

```text
dist/flight-latam-acm-software.zip
```

Esse ZIP é montado por lista branca e inclui código-fonte, documentação, materiais complementares públicos do diretório `data/`, instruções, licença, dependências e configuração pública de exemplo. Ele exclui `config/config.ini`, credenciais, `DADOS_BRUTOS/`, `LOGS/`, caches Python, ambientes virtuais, artefatos locais não curados, screenshots de execução em `LOGS/` e dumps HTML.

## Como Executar

### Interface Gráfica

```powershell
python src\scraper_passagens.py
```

Pela GUI é possível iniciar uma coleta manual, configurar o agendador, ajustar credenciais, ativar evidências de execução e enviar arquivos históricos ao Google Sheets.

### Coleta Padronizada do TCC

```powershell
python src\coleta_tcc.py
```

Esse script executa todas as rotas cadastradas em `ROTAS_DISPONIVEIS` para as janelas `[7, 15, 30, 90]`, em modo headless, com uma repetição por rota e registro sem dados habilitado.

## Demonstração em Vídeo

Confira o funcionamento do sistema em execução na interface gráfica:

[Assista ao vídeo demonstrativo](https://l1nk.dev/0YbEY)

## Configuração

O arquivo de configuração fica em:

```text
config/config.ini
```

Para criar uma configuração local a partir do modelo:

```powershell
copy config\config.example.ini config\config.ini
```

Exemplo:

```ini
[GSHEETS]
arquivo_credenciais = credentials/seu-arquivo-de-chaves.json
id_planilha = ID_DA_PLANILHA_AQUI
nome_aba = Dados

[ANTIDETECT]
humanize = True
save_html = False
screenshot = False

[PROXY]
enabled = False
list =
```

Para usar Google Sheets, crie uma Service Account no Google Cloud, baixe o JSON de credenciais e compartilhe a planilha com o e-mail dessa Service Account.

## Saídas Geradas

- `DADOS_BRUTOS/`: arquivos Excel gerados a cada experimento.
- `LOGS/`: screenshots, HTML bruto compactado e perfis temporários do Chrome.
- Google Sheets: quando configurado, os dados são acrescentados ao final da aba definida.

Quando `save_html=True`, o sistema salva o HTML como `.html.gz`, calcula o MD5 e pode removê-lo após a persistência dos dados, validando o hash antes da exclusão.

## Estrutura do Projeto

```text
Flight-Latam/
|
|-- config/
|   |-- config.example.ini
|   `-- requirements.txt
|
|-- src/
|   |-- scraper_passagens.py
|   |-- latam_scraper.py
|   |-- scrapers_utils.py
|   `-- coleta_tcc.py
|
|-- docs/
|   `-- DOCUMENTACAO_TECNICA.md
|
|-- data/
|   |-- agendador.png
|   |-- analise.xlsx
|   |-- config.png
|   |-- diagrama_sequência_UML.jpeg
|   |-- hist&expo.png
|   |-- inicio.png
|   `-- manual.png
|
|-- scripts/
|   `-- build_submission_zip.ps1
|
|-- DADOS_BRUTOS/
|-- LOGS/
|
|-- README.md
|-- SECURITY.md
`-- LICENSE
```

## Módulos do Código

- `scraper_passagens.py`: interface gráfica, criação do WebDriver, orquestração das coletas, agendador, Excel e Google Sheets.
- `latam_scraper.py`: scraper especializado da LATAM, responsável por abrir a página, localizar cards de voo e extrair o menor preço válido.
- `scrapers_utils.py`: normalização de preços, montagem de registros, geração de IDs, sleeps interrompíveis e funções de hash/limpeza de HTML.
- `coleta_tcc.py`: execução metodológica com todas as rotas e janelas de antecedência do estudo.

## Documentação Técnica

Para detalhes de arquitetura, fluxo de execução, dataset, tratamento de evidências e limitações atuais, consulte:

[Documentação Técnica](docs/DOCUMENTACAO_TECNICA.md)

## Materiais Complementares

- [Planilha de análise](data/analise.xlsx): dataset completo coletado no estudo.
- [Diagrama de sequência UML](data/diagrama_sequência_UML.jpeg): modelagem do fluxo principal do sistema.
- Capturas da interface gráfica: [início](data/inicio.png), [coleta manual](data/manual.png), [agendador](data/agendador.png), [histórico e exportação](<data/hist&expo.png>) e [configuração](data/config.png).

## Segurança

Antes de publicar ou versionar o projeto, revise com atenção:

- não publicar credenciais JSON do Google;
- não publicar IDs de planilhas privadas;
- não publicar `config/config.ini` com dados reais;
- não publicar planilhas geradas em `DADOS_BRUTOS/`, salvo se essa for a intenção;
- não publicar screenshots ou HTML bruto de `LOGS/`, pois podem conter dados de navegação;
- não publicar listas de proxies, senhas ou tokens.

Consulte também [SECURITY.md](SECURITY.md).

## Licença

Este projeto é disponibilizado sob a licença **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`). Consulte o arquivo [LICENSE](LICENSE).
