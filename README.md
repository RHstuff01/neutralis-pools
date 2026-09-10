# Neutralis Pools

Painel local para acompanhar posições de liquidez, com histórico de liquidez, taxas, PnL e APR.

## Recursos

- importa posições abertas da Byreal por carteira pública Solana;
- grava o horário do cadastro como âncora;
- faz a primeira coleta automática 24 horas depois e repete diariamente no mesmo horário;
- interrompe a coleta ao fechar a pool, preservando o histórico;
- mantém os dados no volume local do Umbrel;
- oferece gráficos de liquidez, taxas e APR;
- permite pools manuais e exportação de backup;
- nunca solicita chave privada e não envia transações.

## Instalação por SSH no Umbrel

    git clone https://github.com/RHstuff01/neutralis-pools.git
    cd neutralis-pools
    chmod +x install.sh status.sh stop.sh
    ./install.sh

Abra http://umbrel.local:8788. Se necessário, use o IP local do Umbrel seguido por :8788.

## Community App Store

No Umbrel, acesse **App Store → Community App Stores → Add**, cole a URL abaixo e confirme:

    https://github.com/RHstuff01/neutralis-pools

Depois, abra a loja **Neutralis Apps** e instale o **Neutralis Pools**.

## Desenvolvimento

    NEUTRALIS_POOLS_DATA_DIR=./data STATIC_DIR=./dist PORT=8788 python3 app/server.py

O app usa apenas a biblioteca padrão do Python e os endpoints públicos de leitura da Byreal já utilizados pelo Neutralis Hedge.
