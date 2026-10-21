#!/usr/bin/env python3
"""RADAR SP: sondagem reproducível da base de dotação de 2026 do TCMSP.

NÃO calcula gasto de políticas nem atribui despesas a programas sem validação humana.
Extrai cabeçalhos, candidatas com termos explícitos em campos de programa/ação
 e metadados verificáveis, sem publicar valores somados indevidamente.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import urlopen, Request
import zipfile

URL = 'https://iris.tcm.sp.gov.br/assets/data/dotacao/Dotacao2026.csv'
DEST = Path(__file__).resolve().parents[1] / 'dados' / 'triagem_orcamento_2026.json'
LIMITE = 250 * 1024 * 1024
LIMITE_EXEMPLOS = 40
TERMOS = [
    ('wifi', re.compile(r'\bwi[ -]?fi\b|wi[ -]?fi livre', re.I)),
    ('telecentros', re.compile(r'\btelecentros?\b', re.I)),
    ('inclusao_digital', re.compile(r'inclus[aã]o digital|alfabetiza[cç][aã]o digital', re.I)),
    ('descomplica', re.compile(r'\bdescomplica\b', re.I)),
    ('servicos_digitais', re.compile(r'governo digital|servi[cç]os digitais|transforma[cç][aã]o digital', re.I)),
    ('atendimento_cidadao', re.compile(r'atendimento ao cidad[aã]o', re.I)),
]


def baixar(destino: Path):
    pedido = Request(URL, headers={'User-Agent': 'RADAR-SP-Pesquisa-Exploratoria/0.1 (GitHub público)', 'Accept': 'text/csv,application/octet-stream,*/*'})
    hasher = hashlib.sha256()
    total = 0
    with urlopen(pedido, timeout=90) as resposta, destino.open('wb') as arquivo:
        while bloco := resposta.read(1024 * 1024):
            total += len(bloco)
            if total > LIMITE:
                raise RuntimeError(f'Fonte excedeu limite de {LIMITE} bytes; revisar estratégia de ingestão')
            arquivo.write(bloco)
            hasher.update(bloco)
    if total < 20:
        raise RuntimeError('Fonte vazia ou arquivo incompatível')
    return total, hasher.hexdigest()


def arquivo_csv(fonte: Path, pasta: Path) -> Path:
    with fonte.open('rb') as arquivo:
        magic = arquivo.read(4)
    if magic.startswith(b'PK'):
        with zipfile.ZipFile(fonte) as z:
            candidatos = [item for item in z.infolist() if item.filename.lower().endswith('.csv') and not item.is_dir()]
            if len(candidatos) != 1:
                raise RuntimeError('ZIP sem exatamente um CSV. Inspecionar manualmente')
            info = candidatos[0]
            if info.file_size > LIMITE:
                raise RuntimeError('CSV descompactado excede limite')
            destino = pasta / 'dotacao.csv'
            with z.open(info) as entrada, destino.open('wb') as saida:
                total=0
                while bloco := entrada.read(1024 * 1024):
                    total += len(bloco)
                    if total > LIMITE: raise RuntimeError('CSV excede limite')
                    saida.write(bloco)
            return destino
    if magic[:2] == b'\x1f\x8b':
        destino = pasta / 'dotacao.csv'
        with gzip.open(fonte, 'rb') as entrada, destino.open('wb') as saida:
            total=0
            while bloco := entrada.read(1024 * 1024):
                total += len(bloco)
                if total > LIMITE: raise RuntimeError('CSV excede limite')
                saida.write(bloco)
        return destino
    return fonte


def normalizar(chave: str) -> str:
    from unicodedata import normalize
    return re.sub(r'[^a-z0-9]', '',normalize('NFKD',str(chave)).encode('ascii','ignore').decode('ascii').lower())


def principais_campos(colunas):
    # Correspondência só em descrição de PROGRAMA ou AÇÃO, nunca no nome do órgão isoladamente.
    possiveis = []
    for nome in colunas:
        canon = normalizar(nome)
        if (('programa' in canon or 'projetoatividade' in canon or 'atividade' in canon or 'acao' in canon)
            and (canon.startswith(('ds','desc','nome','nm','titulo')) or 'descricao' in canon)):
            possiveis.append(nome)
    return possiveis


def executar(caminho_fonte: Path, origem_url: str=URL):
    with caminho_fonte.open('rb') as arq:
        inicio = arq.read(20000)
    if b'\x00' in inicio[:4096]: raise RuntimeError('Fonte com bytes binários inesperados; não parece CSV')
    try: inicio.decode('utf-8-sig',errors='strict'); encoding='utf-8-sig'
    except UnicodeDecodeError: encoding='latin-1'
    primeira = inicio.decode(encoding,errors='replace').splitlines()[0]
    sep = max((';',',','\t','|'), key=lambda d:primeira.count(d))
    if primeira.count(sep) < 2: raise RuntimeError('Cabeçalho CSV não reconhecido; precisa análise humana')
    with caminho_fonte.open('r', encoding=encoding, errors='replace', newline='') as arq:
        leitor=csv.DictReader(arq, delimiter=sep)
        campos=leitor.fieldnames or []
        chaves=principais_campos(campos)
        correspondencias=[]
        total=0
        matches=0
        for linha in leitor:
            total+=1
            alvo=' | '.join(linha.get(col,'') or '' for col in chaves)
            grupos=[nome for nome,pat in TERMOS if pat.search(alvo)]
            if not grupos: continue
            matches+=1
            if len(correspondencias) >= LIMITE_EXEMPLOS: continue
            # Preserva apenas os campos descritivos e os códigos identificadores;
            # valores monetários NÃO são somados nem atribuídos a políticas neste estágio.
            resumo={k: (v or '')[:180] for k,v in linha.items() if k and
                (k in chaves or normalizar(k).startswith(('cd','codigo','id')) or 'valor' in normalizar(k) or normalizar(k).startswith('vl'))}
            correspondencias.append({'linha_csv':total+1,'grupos_indicio':grupos,
              'campos':resumo,'vinculo_politica':'correspondencia_textual_a_validar',
              'contabilizar_como_despesa_da_politica':False})
    return {'versao_esquema':'0.1', 'status':'triagem_automatizada_nao_validada',
            'municipio_ibge':'3550308','ano':2026,
            'data_processamento_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'fonte_oficial':origem_url, 'cabecalhos':campos,
            'campos_descritivos_utilizados':chaves,
            'quantidade_linhas_processadas':total,
            'quantidade_correspondencias_textuais':matches,
            'limite_amostra':LIMITE_EXEMPLOS,'amostra':correspondencias,
            'totais_orcamentarios_calculados':False,
            'avisos':['Correspondência textual não prova que o gasto pertence à política.',
                     'Não somar rubricas sem conciliar códigos, unidades, exercício e fases de execução.',
                     'A base pode ter defasagem; validar a data-base na fonte do TCMSP.',
                     'Revisar esquema dos campos antes de calcular autorizado, empenhado, liquidado e pago.']}


def main():
    DEST.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raiz=Path(tmp)
        baixado=raiz/'resposta'
        tamanho,sha=baixar(baixado)
        csvpath=arquivo_csv(baixado,raiz)
        saida=executar(csvpath)
        saida['bytes_download']=tamanho
        saida['sha256_download']=sha
    DEST.write_text(json.dumps(saida,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Fonte:',saida['fonte_oficial'])
    print('Linhas processadas:',saida['quantidade_linhas_processadas'])
    print('Candidatas textuais:',saida['quantidade_correspondencias_textuais'])
    print('Campos descritivos:',saida['campos_descritivos_utilizados'])
    print('Resultado:',DEST)
    print('Aviso: não foram calculados totais orçamentários nem atribuições a políticas.')

if __name__=='__main__': main()
