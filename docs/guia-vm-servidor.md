# Guia Passo a Passo: Criando e Configurando uma VM Linux no VirtualBox para o SmartEvents Server

Este guia orienta na criação de uma Máquina Virtual (VM) Linux com Ubuntu Server utilizando o **VirtualBox** no Windows, configurando a rede em modo *Bridge* para que outros computadores da rede possam acessar a API e a interface web de cadastro de eventos do SmartEvents.

---

## 📋 Pré-requisitos

1. **VirtualBox** instalado no computador hospedeiro (Windows).
   - [Download do Oracle VM VirtualBox](https://www.virtualbox.org/wiki/Downloads)
2. **ISO do Ubuntu Server** (recomenda-se a versão LTS mais recente, ex: Ubuntu Server 24.04 LTS).
   - [Download da ISO do Ubuntu Server](https://ubuntu.com/download/server)

---

## 🛠️ Passo 1: Criando a VM no VirtualBox

1. Abra o **VirtualBox** e clique no botão **Novo** (New).
2. Configure os campos iniciais:
   - **Nome:** `SmartEvents-Server`
   - **Pasta da VM:** Mantenha o padrão ou escolha um local com espaço em disco.
   - **Imagem ISO:** Selecione o arquivo `.iso` do Ubuntu Server que você baixou.
   - **Tipo:** Linux
   - **Versão:** Ubuntu (64-bit)
   - Clique em **Próximo**.
3. **Instalação Desatendida (Unattended Install):**
   - Recomendamos marcar a caixa **Pular Instalação Desatendida** (Skip Unattended Installation) para que você configure o sistema manualmente e defina suas credenciais com segurança. Clique em **Próximo**.
4. **Hardware (Recursos):**
   - **Memória RAM:** Defina pelo menos `2048 MB` (2 GB).
   - **Processadores:** Aloque pelo menos `2 CPUs` (se seu processador hospedeiro permitir).
   - Clique em **Próximo**.
5. **Disco Rígido Virtual:**
   - Selecione **Criar um Disco Rígido Virtual Agora**.
   - Defina o tamanho do disco (mínimo de `20 GB` é suficiente para este servidor e banco de dados).
   - Clique em **Próximo** e depois em **Finalizar**.

---

## ⚙️ Passo 2: Configurações Críticas da VM

Antes de iniciar a máquina virtual pela primeira vez, realize as seguintes configurações:

### 1. Configuração de Rede (Modo Bridge) - OBRIGATÓRIO
1. Selecione a VM `SmartEvents-Server` na lista lateral esquerda e clique em **Configurações** (Settings).
2. Vá na aba **Rede** (Network).
3. No campo **Conectado a:** (Attached to), altere de *NAT* para **Placa em Ponte** (Bridge Adapter).
4. No campo **Nome** (Name), selecione a sua placa de rede ativa do computador hospedeiro (se você usa cabo, selecione a placa Ethernet; se usa Wi-Fi, selecione a placa Wireless).
5. Clique em **OK** para salvar.

> [!IMPORTANT]
> O modo *Placa em Ponte (Bridge)* permite que a VM se conecte diretamente ao seu roteador físico, obtendo um IP individual na sua rede local. Sem isso, outros computadores não conseguirão se conectar ao servidor.

---

## 💿 Passo 3: Instalando o Ubuntu Server

1. Com a VM selecionada no VirtualBox, clique em **Iniciar** (Start).
2. O instalador do Ubuntu Server iniciará. Siga os passos na tela:
   - **Idioma:** Escolha seu idioma preferido (Português ou Inglês).
   - **Layout do Teclado:** Selecione a opção correspondente ao seu teclado (geralmente Portuguese - Brazil).
   - **Tipo de Instalação:** Escolha **Ubuntu Server** (padrão).
   - **Conexões de Rede:** O instalador detectará um IP dinâmico via DHCP (anote este IP se possível, ex: `192.168.1.150`).
   - **Configuração do Proxy e Mirror:** Mantenha as opções padrões.
   - **Armazenamento:** Selecione "Use an entire disk" (Usar o disco inteiro) e confirme.
   - **Perfil (Profile Setup):** Preencha suas informações:
     - **Seu Nome:** Digite seu nome.
     - **Nome do Servidor (Hostname):** `smartevents-server`
     - **Usuário (Username):** Escolha um usuário administrativo (ex: `adminuser`).
     - **Senha (Password):** Defina uma senha forte.
   - **SSH Setup:** Marque a opção **Install OpenSSH Server** (Instalar servidor OpenSSH) pressionando a barra de espaço. Isso é necessário para transferir arquivos e gerenciar o servidor de longe.
   - **Featured Server Snaps:** Não selecione nada, apenas avance.
3. Aguarde a conclusão da instalação. Ao finalizar, selecione **Reboot Now** (Reiniciar agora).
4. Pressione `Enter` quando o instalador solicitar que você remova a mídia de instalação.

---

## 📂 Passo 4: Transferindo os Arquivos do Projeto para a VM

Agora que a VM está rodando e conectada à rede, você precisará transferir os arquivos do servidor do seu computador Windows para a VM Linux.

1. Descubra o IP da sua VM rodando o seguinte comando no terminal da VM:
   ```bash
   ip a
   ```
   Procure pelo endereço IPv4 associado à sua interface de rede ativa (ex: `192.168.1.150`).
2. Abra o terminal (PowerShell) no seu computador **Windows**, navegue até a pasta do projeto SmartEvents e use o comando `scp` para enviar a pasta do servidor para a VM:
   ```powershell
   # Envia o script do servidor
   scp server.py adminuser@192.168.1.150:/home/adminuser/
   
   # Envia o requirements.txt
   scp requirements.txt adminuser@192.168.1.150:/home/adminuser/
   
   # Envia a pasta de frontend da web
   scp -r server_frontend adminuser@192.168.1.150:/home/adminuser/
   ```
   *(Substitua `adminuser` e `192.168.1.150` pelo usuário e IP configurados na sua VM).*

---

## 🐍 Passo 5: Configurando o Python e Dependências no Linux

Conecte-se à VM Linux (via SSH ou no próprio terminal da VM no VirtualBox) e execute:

```bash
# 1. Atualizar pacotes do sistema
sudo apt update && sudo apt upgrade -y

# 2. Instalar o Python3, pip e suporte a ambiente virtual
sudo apt install python3 python3-pip python3-venv -y

# 3. Mover-se para a pasta pessoal
cd /home/adminuser

# 4. Criar o ambiente virtual Python
python3 -m venv .venv

# 5. Ativar o ambiente virtual
source .venv/bin/activate

# 6. Instalar as dependências do SmartEvents Server (fastapi, uvicorn, requests, etc.)
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 🚀 Passo 6: Configurando o Servidor como Serviço do Sistema (systemd)

Para que o servidor inicie automaticamente toda vez que a máquina virtual for ligada (e reinicie sozinho caso ocorra algum erro), vamos criá-lo como um serviço do sistema Linux.

1. Crie o arquivo de configuração do serviço:
   ```bash
   sudo nano /etc/systemd/system/smartevents.service
   ```
2. Cole o conteúdo abaixo (ajuste `/home/adminuser` se o seu nome de usuário da VM for diferente):
   ```ini
   [Unit]
   Description=SmartEvents Central API Server
   After=network.target

   [Service]
   User=adminuser
   WorkingDirectory=/home/adminuser
   ExecStart=/home/adminuser/.venv/bin/python3 /home/adminuser/server.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
3. Salve o arquivo (no nano: pressione `Ctrl + O`, depois `Enter` para confirmar, e `Ctrl + X` para sair).
4. Recarregue o sistema de serviços, inicie o servidor e ative a inicialização automática:
   ```bash
   # Recarrega configurações do systemd
   sudo systemctl daemon-reload

   # Habilita para iniciar no boot do sistema
   sudo systemctl enable smartevents.service

   # Inicia o servidor agora
   sudo systemctl start smartevents.service
   ```
5. Verifique se o servidor está rodando com sucesso:
   ```bash
   sudo systemctl status smartevents.service
   ```

---

## 🔒 Passo 7: Configurando o Firewall da VM Linux

Se a VM estiver usando o firewall interno (`ufw`), você precisará liberar a porta `8000`:

```bash
# Permite tráfego de entrada na porta 8000 (TCP)
sudo ufw allow 8000/tcp

# Habilita o firewall (se já não estiver)
sudo ufw enable
```

---

## 🎯 Passo 8: Validação e Teste Final

1. **Acesso Web:** No seu navegador no Windows (ou em qualquer outro computador conectado na mesma rede física), acesse:
   `http://<IP_DA_VM_LINUX>:8000`
   - A interface web premium do SmartEvents Central com suporte a drag-and-drop de arquivos JSON deve carregar com sucesso.
2. **Integração Desktop:** No executável do SmartEvents cliente, abra a engrenagem de configurações de sincronização e defina a URL do servidor como:
   `http://<IP_DA_VM_LINUX>:8000`
   - Salve as configurações. Os eventos cadastrados na página web devem agora ser sincronizados e listados automaticamente no SmartEvents Desktop!
