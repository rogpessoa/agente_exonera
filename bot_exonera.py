import os
import requests
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import urllib3
from openai import OpenAI
import re
from time import sleep

# Silencia os alertas
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Carrega as configurações do arquivo .env
load_dotenv()

def baixar_diario_de_hoje():
    url_base = "https://dom.pmvc.ba.gov.br"
    
    try:
        response = requests.get(url_base, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        primeiro_box = soup.find('div', class_='box-diario')
        if not primeiro_box:
            print("Nenhuma estrutura 'box-diario' encontrada.")
            return None
            
        id_diario = primeiro_box.get('id', 'diario-desconhecido')
        print(f"Edição mais recente detectada: {id_diario}")
        
        # Busca especificamente o botão principal "Baixar Publicação"
        link_tag = primeiro_box.find('a', attrs={"data-original-title": "Baixar Publicação"})
        if not link_tag:
            link_tag = primeiro_box.find('a', title="Baixar Publicação")
            
        if not link_tag:
            print("Botão 'Baixar Publicação' não encontrado.")
            return None
            
        link_original = link_tag['href']
        if not link_original.startswith('http'):
            link_original = url_base.rstrip('/') + '/' + link_original.lstrip('/')
            
        print(f"Link do botão extraído: {link_original}")
        
        # --- SISTEMA ANTIDUPLICIDADE ---
        arquivo_controle = "ultimo_diario_enviado.txt"
        if os.path.exists(arquivo_controle):
            with open(arquivo_controle, 'r') as f:
                ultimo_enviado = f.read().strip()
            if link_original == ultimo_enviado:
                print("Esta edição já foi enviada no WhatsApp. Nenhuma publicação nova hoje.")
                return None

        print("Acessando o link para extrair o arquivo bruto...")
        
        resp = requests.get(link_original, verify=False)
        conteudo_pdf = None
        
        # Estratégia 1: O link original já é o PDF?
        if resp.content.startswith(b'%PDF'):
            print("Estratégia 1 funcionou: PDF baixado diretamente!")
            conteudo_pdf = resp.content
        else:
            # Estratégia 2: É o visualizador HTML. Vamos varrer o código atrás de links .pdf
            print("Visualizador detectado. Varrendo o HTML em busca do link .pdf real...")
            match = re.search(r'(https?://[^\s"\'<>]+?\.pdf|/[^\s"\'<>]+?\.pdf)', resp.text, re.IGNORECASE)
            
            if match:
                link_interno = match.group(1)
                if not link_interno.startswith('http'):
                    link_interno = url_base.rstrip('/') + '/' + link_interno.lstrip('/')
                print(f"Link interno encontrado: {link_interno}")
                
                resp2 = requests.get(link_interno, verify=False)
                if resp2.content.startswith(b'%PDF'):
                    print("Estratégia 2 funcionou: PDF interno baixado!")
                    conteudo_pdf = resp2.content
            
            # Estratégia 3: O truque da URL (trocar previsualizar por baixar/download)
            if not conteudo_pdf and "/previsualizar/" in link_original:
                link_forcar_download = link_original.replace("/previsualizar/", "/baixar/")
                print(f"Tentando Estratégia 3 (forçar rota de download): {link_forcar_download}")
                
                resp3 = requests.get(link_forcar_download, verify=False)
                if resp3.content.startswith(b'%PDF'):
                    print("Estratégia 3 funcionou: Rota de download descoberta!")
                    conteudo_pdf = resp3.content

        if not conteudo_pdf:
            print("Falha severa: Não foi possível extrair o PDF bruto.")
            return None

        nome_arquivo = "diario_novo.pdf"
        with open(nome_arquivo, 'wb') as f:
            f.write(conteudo_pdf)
            
        with open(arquivo_controle, 'w') as f:
            f.write(link_original)
            
        print(f"Sucesso absoluto! Arquivo salvo como: {nome_arquivo}")
        return nome_arquivo
        
    except Exception as e:
        print(f"Erro inesperado durante a extração: {e}")
        return None

def extrair_texto(pdf_path):
    print("Extraindo texto do PDF...")
    texto_completo = ""
    try:
        with fitz.open(pdf_path) as doc:
            for pagina in doc:
                texto_completo += pagina.get_text()
        return texto_completo
    except Exception as e:
        print(f"Erro ao extrair texto do PDF: {e}")
        return ""


def resumir_com_ia(texto):
    if not texto.strip():
        return None
        
    prompt = f"""
    Você é um auditor de inteligência focado em Recursos Humanos, analisando o Diário Oficial de Vitória da Conquista.
    Sua ÚNICA missão é rastrear e extrair atos de EXONERAÇÃO (quando um servidor é desligado de um cargo comissionado ou efetivo).
    
    Abaixo está o texto do Diário Oficial do Município de hoje.
    
    -- REGRAS RÍGIDAS DE EXTRAÇÃO --
    1. FOCO EXCLUSIVO EM DECRETOS: As exonerações SEMPRE são publicadas no formato de "Decretos". Ignore o resto do documento (licitações, portarias, editais) e concentre sua análise na seção de Decretos.
    2. VOCÊ DEVE VERIFICAR SE O SERVIDOR FOI EXONERADO DE UM CARGO E RECONTRATADO EM OUTRO CARGO, NESSES CASOS ELE NÃO DEVE TER O NOME PUBLICADO
    3. FILTRO DE VAZIO: Se você ler os decretos e não encontrar NENHUMA exoneração, você deve responder EXATAMENTE com a seguinte frase: "Nenhuma exoneração foi publicada nos Decretos do Diário de hoje. ✅"
    4. DADOS NECESSÁRIOS: Para cada exoneração encontrada nos decretos, extraia:
       - Nome do servidor
       - Cargo que ocupava
       - Secretaria/Órgão de lotação
       - Número do Decreto (ex: Decreto nº 24.205)
    
    -- REGRAS DE FORMATAÇÃO PARA WHATSAPP --
    4. LISTAS: Use OBRIGATORIAMENTE o caractere `•` (bolinha) para iniciar os itens. NUNCA use o asterisco (`*`) ou traços.
    5. AGRUPAMENTO: Agrupe as exonerações por Secretaria. Use negrito no nome da secretaria. Exemplo: 🏢 *Secretaria de Saúde*
    6. SEM GORDURA: Não escreva introduções ou conclusões. Vá direto aos dados extraídos ou à mensagem de vazio do item 2.
    
    Texto do Diário:
    {texto}
    """
    
    print("Enviando texto para o Radar de Exonerações na OpenAI (gpt-4o-mini)...")
    
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.0 # Temperatura zerada para não inventar nomes
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        print(f"Erro ao conectar com a OpenAI: {e}")
        return "Erro de conexão com o servidor da IA."

def enviar_whatsapp(mensagem):
    print("Enviando mensagem para o WhatsApp...")
    base_url = os.getenv('EVOLUTION_API_URL').rstrip('/')
    instancia = os.getenv('EVOLUTION_INSTANCE')
    
    url = f"{base_url}/message/sendText/{instancia}"
    
    headers = {
        "apikey": os.getenv("EVOLUTION_API_KEY"),
        "Content-Type": "application/json"
    }
    
    payload = {
        "number": os.getenv("WHATSAPP_DESTINO"),
        "text": f"📰 *Resumo do Diário Oficial de Conquista*\n\n{mensagem}"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print("Mensagem enviada com sucesso no WhatsApp!")
    except Exception as e:
        print(f"Erro ao enviar WhatsApp: {e}")

# --- FLUXO PRINCIPAL ---

if __name__ == "__main__":
    print("Iniciando Radar de Exonerações do Diário Oficial...")
    arquivo_pdf = baixar_diario_de_hoje()
    
    if arquivo_pdf:
        texto_diario = extrair_texto(arquivo_pdf)
        
        if texto_diario:
            print("Analisando os Decretos em busca de exonerações...")
            resultado_ia = resumir_com_ia(texto_diario)
            
            if resultado_ia and "Erro de conexão" not in resultado_ia:
                # O robô agora envia o relatório todos os dias (com as exonerações ou com o aviso de que não houve)
                mensagem_final = f"🚨 *Radar de Exonerações - DOM*\n\n{resultado_ia.strip()}"
                enviar_whatsapp(mensagem_final)
            else:
                print("Houve uma falha na IA e a mensagem não foi gerada corretamente.")
                
        # Limpa o arquivo PDF baixado para não lotar o servidor
        os.remove(arquivo_pdf)
    else:
        print("Fim da execução. Nenhuma ação necessária.")