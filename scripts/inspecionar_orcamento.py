#!/usr/bin/env python3
"""RADAR SP | Sondagem orçamentária 0.2 (sem atribuição de despesas).

Consulta dotações do TCMSP e produz candidatos para revisão humana. Preserva valores
brutos por linha e jamais calcula totais ou afirma impacto da política.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen
import zipfile

URL = 'https://iris.tcm.sp.gov.br/assets/data/dotacao/Dotacao2026.csv'
DEST = Path(__file__).resolve().parents[1] / 'dados' / 'triagem_orcamento_2026.json'
LIMITE_BYTES = 250 * 1024 * 1024
LIMITE_AMOSTRA = 100
TERMOS = [
    ('wifi', re.compile(r'\bwi[ -]?fi\b|wi[ -]?fi livre', re.I)),
    ('telecentros', re.compile(r'\btelecentros?\b', re.I)),
    ('inclusao_digital', re.compile(r'inclus[aã]o digital|alfabetiza[cç][aã]o digital', re.I)),
    ('descomplica', re.compile(r'\bdescomplica\b', re.I)),
    ('servicos_digitais', re.compile(r'governo digital|servi[cç]os digitais|transforma[cç][aã]o digital', re.I)),
    ('atendimento_cidadao', re.compile(r'atendimento ao cidad[aã]o', re.I)),
]
FINANCEIROS = {
    'proposto', 'orcado', 'atualizado', 'empenhadobruto', 'anulado',
    'empenhadoliquido', 'liquidado', 'pago', 'congelado',
    'restossaldotransferido', 'restosprocessados', 'restosnaoprocessados',
    'restosliquidado', 'restospago', 'restoscancelado', 'restossaldo',
}


def baixar(destino: Path) -> tuple[int, str]:
    req = Request(URL, headers={
        'User-Agent': 'RADAR-SP-Pesquisa-Documental/0.2 (GitHub publico)',
        'Accept': 'text/csv,application/octet-stream,*/*',
    })
    sha = hashlib.sha256()
    n = 0
    with urlopen(req, timeout=90) as resposta, destino.open('wb') as out:
        while bloco := resposta.read(1024 * 1024):
            n += len(bloco)
            if n > LIMITE_BYTES:
                raise RuntimeError('Download acima do limite estabelecido')
            out.write(bloco)
            sha.update(bloco)
    if n < 20:
        raise RuntimeError('Fonte vazia ou inesperadamente curta')
    return n, sha.hexdigest()


def descompactar(fonte: Path, pasta: Path) -> Path:
    with fonte.open('rb') as f:
        magic = f.read(4)
    if magic.startswith(b'PK'):
        with zipfile.ZipFile(fonte) as z:
            files = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith('.csv')]
            if len(files) != 1 or files[0].file_size > LIMITE_BYTES:
                raise RuntimeError('ZIP incompatível, ambíguo ou grande demais')
            destino = pasta / 'dados.csv'
            entrada = z.open(files[0])
            with entrada, destino.open('wb') as out:
                copiar_limitado(entrada, out)
            return destino
    if magic.startswith(b'\x1f\x8b'):
        destino = pasta / 'dados.csv'
        with gzip.open(fonte, 'rb') as entrada, destino.open('wb') as out:
            copiar_limitado(entrada, out)
        return destino
    return fonte


def copiar_limitado(entrada, saida):
    n = 0
    while bloco := entrada.read(1024 * 1024):
        n += len(bloco)
        if n > LIMITE_BYTES:
            raise RuntimeError('CSV descompactado acima do limite')
        saida.write(bloco)


def normalizar(txt: str) -> str:
    from unicodedata import normalize
    txt = normalize('NFKD', str(txt)).encode('ascii', 'ignore').decode('ascii').lower()
    return re.sub('[^a-z0-9]', '', txt)


def colunas_descritivas(cabecalhos: list[str]) -> list[str]:
    escolhidas = []
    for col in cabecalhos:
        c = normalizar(col)
        if (('programa' in c or 'projetoatividade' in c or 'atividade' in c or 'acao' in c)
                and (c.startswith(('ds', 'desc', 'nome', 'nm', 'titulo')) or 'descricao' in c)):
            escolhidas.append(col)
    return escolhidas


def determinar_codificacao(amostra: bytes) -> str:
    try:
        amostra.decode('utf-8-sig', 'strict')
        return 'utf-8-sig'
    except UnicodeDecodeError:
        # A fonte examinada em 20/10/2026 continha trechos no padrão CP850
        # (ex.: Manuten\x87\xc6o = Manutenção). Revisar se o portal mudar o formato.
        return 'cp850'


def processar(caminho: Path, origem_url: str = URL) -> dict:
    with caminho.open('rb') as f:
        amostra = f.read(24000)
    if b'\x00' in amostra[:4096]:
        raise RuntimeError('Arquivo não se parece com CSV textual')
    encoding = determinar_codificacao(amostra)
    linha = amostra.decode(encoding, errors='replace').splitlines()[0]
    sep = max((';', ',', '\t', '|'), key=lambda p: linha.count(p))
    if linha.count(sep) < 2:
        raise RuntimeError('Separador CSV não reconhecido')

    with caminho.open('r', encoding=encoding, errors='replace', newline='') as f:
        reader = csv.DictReader(f, delimiter=sep)
        cabecalhos = reader.fieldnames or []
        descricoes = colunas_descritivas(cabecalhos)
        if not descricoes:
            raise RuntimeError('Nenhuma coluna descritiva de programa/atividade identificada')
        nomes_valor = [col for col in cabecalhos if normalizar(col) in FINANCEIROS]
        # A coluna de orçamento inicial às vezes tem acentuação danificada na origem.
        # Só identificamos pela posição se vier entre 'Proposto' e 'Atualizado'.
        for i in range(1, len(cabecalhos)-1):
            if (normalizar(cabecalhos[i-1]) == 'proposto'
                    and normalizar(cabecalhos[i+1]) == 'atualizado'
                    and cabecalhos[i] not in nomes_valor):
                nomes_valor.append(cabecalhos[i])
        if not nomes_valor:
            raise RuntimeError('Campos financeiros não reconhecidos; conferir layout')

        contagem = Counter()
        acao_contagem = Counter()
        exemplos = []
        total = 0
        candidatos = 0
        for registro in reader:
            total += 1
            desc = ' | '.join(registro.get(c, '') or '' for c in descricoes)
            grupos = [g for g, p in TERMOS if p.search(desc)]
            if not grupos:
                continue
            candidatos += 1
            contagem.update(grupos)
            cod_acao = next((v for k, v in registro.items() if k and
                'projetoatividade' in normalizar(k) and
                (normalizar(k).startswith(('cd', 'codigo')))), '')
            if cod_acao:
                acao_contagem[cod_acao] += 1
            if len(exemplos) >= LIMITE_AMOSTRA:
                continue
            campos = {k: (v or '')[:200] for k, v in registro.items() if k and
                (k in descricoes or normalizar(k).startswith(('cd', 'codigo', 'id')))}
            valores = {k: (registro.get(k, '') or '')[:80] for k in nomes_valor}
            exemplos.append({
                'linha_csv': total + 1,
                'grupos_indicio': grupos,
                'campos': campos,
                'valores_brutos_por_linha': valores,
                'vinculo_politica': 'correspondencia_textual_a_validar',
                'contabilizar_como_despesa_da_politica': False,
            })

    return {
        'versao_esquema': '0.2',
        'status': 'triagem_automatizada_nao_validada',
        'municipio_ibge': '3550308', 'ano': 2026,
        'data_processamento_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'fonte_oficial': origem_url,
        'codificacao_entrada_utilizada': encoding,
        'cabecalhos': cabecalhos,
        'campos_descritivos_utilizados': descricoes,
        'campos_financeiros_preservados': nomes_valor,
        'quantidade_linhas_processadas': total,
        'quantidade_correspondencias_textuais': candidatos,
        'correspondencias_por_grupo': dict(contagem),
        'correspondencias_por_codigo_acao': dict(acao_contagem),
        'limite_amostra': LIMITE_AMOSTRA,
        'amostra': exemplos,
        'totais_orcamentarios_calculados': False,
        'avisos': [
            'Valores brutos por linha servem à auditoria; não são somados nesta fase.',
            'Correspondência textual não demonstra execução financeira ou vínculo causal.',
            'Identificar possíveis duplicidades, valores negativos, fonte e fase contábil.',
            'Validar períodos, códigos de ação e vínculos normativos antes de publicar totais.',
            'Se os textos vierem com caracteres estranhos, conferir codificação da fonte.',
        ],
    }


def main():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        fonte = raiz / 'download'
        tamanho, sha = baixar(fonte)
        csv_path = descompactar(fonte, raiz)
        saida = processar(csv_path)
        saida['bytes_download'] = tamanho
        saida['sha256_download'] = sha
    DEST.write_text(json.dumps(saida, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Fonte:', saida['fonte_oficial'])
    print('Codificacao:', saida['codificacao_entrada_utilizada'])
    print('Linhas:', saida['quantidade_linhas_processadas'])
    print('Candidatos:', saida['quantidade_correspondencias_textuais'])
    print('Grupos:', saida['correspondencias_por_grupo'])
    print('Campos financeiros brutos:', saida['campos_financeiros_preservados'])
    print('Resultado:', DEST)
    print('IMPORTANTE: sem valores agregados, sem atribuicao a politica, sem inferencia de impacto.')


if __name__ == '__main__':
    main()
