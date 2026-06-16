# Validação de Conexão VPN no Sistema MDT

A validação de conectividade à VPN é realizada de forma automática em duas telas distintas da interface gráfica: a tela de **Baixar Coleta MDT** ([DownloadPage](file:///c:/Users/a50057663/Desktop/Automações/MDT/app/ui/download_page.py#L21)) e a tela de **EP Generator** ([EpPage](file:///c:/Users/a50057663/Desktop/Automações/MDT/app/ui/ep_page.py#L25)).

Abaixo está o detalhamento técnico de como esse processo funciona.

---

## 1. IPs Utilizados para Validação

Cada tela possui um IP de destino específico (geralmente associado a servidores internos/bancos de dados do ecossistema) para validar se a rota está ativa:
*   **Tela de Coleta / Download** ([DownloadPage](file:///c:/Users/a50057663/Desktop/Automações/MDT/app/ui/download_page.py#L17)): Tenta alcançar o IP `10.220.50.21`.
*   **Tela do EP Generator** ([EpPage](file:///c:/Users/a50057663/Desktop/Automações/MDT/app/ui/ep_page.py#L16)): Tenta alcançar o IP `10.226.99.116`.

---

## 2. Fluxo da Validação na Interface (UI)

Quando o usuário clica no botão para iniciar a ação (por exemplo, no método `_start` de [DownloadPage](file:///c:/Users/a50057663/Desktop/Automações/MDT/app/ui/download_page.py#L281)):

1.  O botão de início é desabilitado e seu texto muda para **"Verificando VPN..."**.
2.  É impresso no console de logs da interface que a conectividade está sendo testada.
3.  A função assíncrona de verificação ([_check_vpn_async](file:///c:/Users/a50057663/Desktop/Automações/MDT/app/ui/download_page.py#L331)) é disparada.
4.  **Se a conexão for bem-sucedida:** O log exibe `"[VPN] Conexão OK."` e o download/geração prossegue normalmente.
5.  **Se a conexão falhar:** Exibe-se a mensagem de erro `"[ERRO] VPN não detectada. Não foi possível alcançar <IP>. Conecte-se à VPN e tente novamente."` e a ação é abortada, habilitando o botão de início novamente.

---

## 3. Implementação Técnica (`_check_vpn_async`)

A validação é feita de maneira assíncrona em uma thread secundária para que a interface gráfica (feita em CustomTkinter) não trave ("congele") enquanto aguarda o retorno da rede.

Veja o código do método em [_check_vpn_async](file:///c:/Users/a50057663/Desktop/Automações/MDT/app/ui/download_page.py#L331-L347):

```python
    def _check_vpn_async(self, on_result):
        def worker():
            try:
                # Evita que uma janela do prompt CMD abra no Windows
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                
                # Executa o comando ping (4 pacotes) no sistema operacional
                result = subprocess.run(
                    ["ping", "-n", str(VPN_PING_COUNT), VPN_CHECK_IP],
                    capture_output=True,
                    timeout=VPN_PING_COUNT * 2 + 10,
                    creationflags=flags,
                )
                output = (result.stdout or b"") + (result.stderr or b"")
                
                # Valida se obteve respostas válidas verificando a presença de "TTL=" no output
                ok = b"TTL=" in output.upper()
            except Exception:
                ok = False
            
            # Retorna o resultado para a thread principal da interface
            self.after(0, lambda: on_result(ok))

        threading.Thread(target=worker, daemon=True).start()
```

### Pontos Importantes do Código:
*   **`subprocess.run`**: Executa o comando de ping nativo do sistema operacional (`ping -n 4 <IP>`).
*   **`subprocess.CREATE_NO_WINDOW`**: Utiliza flags de criação no Windows para garantir que nenhuma tela preta de terminal do prompt apareça de surpresa para o usuário.
*   **Validação por `TTL=`**: O sistema analisa a string de retorno do comando. Em sistemas operacionais Windows, o ping bem-sucedido retorna parâmetros de rede contendo `TTL=` (Time to Live). A presença desse termo no output confirma que o pacote foi recebido de volta do IP de destino, sinalizando que a VPN está ativa.
*   **`self.after(0, ...)`**: Garante que a chamada de retorno (`callback`) seja executada com segurança de threads dentro do loop principal do Tkinter.
