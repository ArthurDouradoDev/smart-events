import os
import re
import json
import time
import requests
import urllib3
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).parent.parent
SESSION_PATH = ROOT / "data" / "session.json"
REQUESTS_DIR = ROOT / "requests"

def parse_curl_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Clean up line continuations
    content = re.sub(r'[\^\\]\r?\n', ' ', content)
    
    # Remove all carets first (CMD escape characters)
    content = content.replace("^", "")
    
    # Clean up escaped quotes
    content = content.replace('\\"', '"')
    
    # Extract URL (everything up to space or quote)
    url_match = re.search(r'(https?://10\.220\.50\.9:31943[^\s"]*)', content)
    url = url_match.group(1) if url_match else None
        
    # Extract headers
    headers = {}
    h_matches = re.findall(r'-H\s+("[^"]*"|\'[^\']*\'|[^\s]+)', content)
    for h in h_matches:
        h_clean = h.strip('"\'')
        if ":" in h_clean:
            name, val = h_clean.split(":", 1)
            headers[name.strip()] = val.strip()
            
    # Extract cookies
    cookie_match = re.search(r'-b\s+("[^"]*"|\'[^\']*\'|[^\s]+)', content)
    cookies_str = cookie_match.group(1).strip('"\'') if cookie_match else ""
    
    # Extract data-raw (JSON body)
    data_str = None
    if "--data-raw" in content:
        data_part = content.split("--data-raw")[-1]
        first_idx = min(data_part.find('{'), data_part.find('['))
        if first_idx == -1:
            first_idx = max(data_part.find('{'), data_part.find('['))
        last_idx = max(data_part.rfind('}'), data_part.rfind(']'))
        if first_idx != -1 and last_idx != -1:
            data_str = data_part[first_idx:last_idx+1]
            
    return url, headers, cookies_str, data_str

def run_test():
    print("=" * 80)
    print(" SmartEvents - Teste de Validação das Requisições")
    print("=" * 80)
    
    if not SESSION_PATH.exists():
        print(f"[ERRO] Arquivo de sessão não encontrado em {SESSION_PATH}")
        print("Por favor, execute o get_session.py primeiro.")
        return
        
    with open(SESSION_PATH, "r", encoding="utf-8") as f:
        sess_data = json.load(f)
        
    active_cookies = sess_data["trace"]["cookies"]
    active_roarand = sess_data["trace"]["roarand"]
    
    # Find all txt files in requests folder
    txt_files = []
    for root, dirs, files in os.walk(REQUESTS_DIR):
        for file in files:
            if file.endswith(".txt"):
                txt_files.append(Path(root) / file)
                
    if not txt_files:
        print("Nenhum arquivo de requisição (.txt) encontrado na pasta 'requests'.")
        return
        
    print(f"Encontrados {len(txt_files)} arquivos de requisição para testar.\n")
    
    success_count = 0
    fail_count = 0
    
    for idx, fpath in enumerate(sorted(txt_files), 1):
        rel_path = fpath.relative_to(ROOT)
        print(f"[{idx}/{len(txt_files)}] Testando: {rel_path}")
        
        try:
            url, headers, cookies_str, data_str = parse_curl_file(fpath)
            if not url:
                print(f"  [AVISO] Não foi possível extrair a URL do arquivo.")
                fail_count += 1
                continue
                
            # Parse cookies from curl command
            cookies_dict = {}
            if cookies_str:
                for part in cookies_str.split(";"):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        cookies_dict[k.strip()] = v.strip()
                        
            # Override/update with active cookies
            for ac in active_cookies:
                cookies_dict[ac["name"]] = ac["value"]
                
            # Build request session
            s = requests.Session()
            s.verify = False
            for k, v in cookies_dict.items():
                s.cookies.set(k, v, domain="10.220.50.9")
                
            # Set headers, override roarand with active CSRF token
            req_headers = {k: v for k, v in headers.items()}
            req_headers["roarand"] = active_roarand
            
            # Perform request
            start_time = time.time()
            if data_str:
                # POST request
                resp = s.post(url, headers=req_headers, data=data_str, timeout=15)
            else:
                # GET request
                resp = s.get(url, headers=req_headers, timeout=15)
            duration = time.time() - start_time
            
            # Print results
            print(f"  URL: {url.split('?')[0]}")
            print(f"  Método: {'POST' if data_str else 'GET'}")
            print(f"  Status Code: {resp.status_code} ({resp.reason}) | Tempo: {duration:.2f}s")
            
            # Check response body status or content type
            ct = resp.headers.get("Content-Type", "")
            print(f"  Content-Type: {ct}")
            
            is_success = False
            if resp.status_code == 200:
                try:
                    resp_json = resp.json()
                    is_success = True
                    
                    # Check for server-side error wrappers in JSON
                    if isinstance(resp_json, dict):
                        if resp_json.get("success") is False or resp_json.get("ok") is False:
                            print(f"  [AVISO] Resposta retornou status de falha no JSON: {resp_json}")
                        elif "checkState" in resp_json and resp_json.get("checkState") is False:
                            print(f"  [AVISO] pre-check retornou checkState False (talvez taskId expirada/inválida)")
                        elif "code" in resp_json and resp_json.get("code") != 0 and str(resp_json.get("code")).lower() != "success":
                            print(f"  [AVISO] Código de retorno inesperado: {resp_json.get('code')}")
                            
                    # Print preview of response
                    json_preview = json.dumps(resp_json, indent=2, ensure_ascii=False)
                    print(f"  Resposta JSON (primeiras 5 linhas):")
                    lines = json_preview.splitlines()
                    for line in lines[:5]:
                        print(f"    {line}")
                    if len(lines) > 5:
                        print("    ...")
                except Exception:
                    # Non-JSON or parsing error
                    is_success = True
                    body_preview = resp.text[:150]
                    print(f"  Resposta Texto (primeiros 150 caracteres): {body_preview!r}")
            else:
                print(f"  [ERRO] Falha na requisição. Resposta: {resp.text[:200]!r}")
                
            if is_success:
                success_count += 1
            else:
                fail_count += 1
                
        except Exception as e:
            print(f"  [EXCEÇÃO] Erro ao executar teste: {e}")
            fail_count += 1
            
        print("-" * 80)
        
    print("\n" + "=" * 80)
    print(f" Resumo dos Testes:")
    print(f" Total de arquivos testados: {len(txt_files)}")
    print(f" Sucessos (HTTP 200): {success_count}")
    print(f" Falhas: {fail_count}")
    print("=" * 80)

if __name__ == "__main__":
    run_test()
