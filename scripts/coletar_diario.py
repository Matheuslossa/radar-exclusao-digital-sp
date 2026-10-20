#!/usr/bin/env python3
"""Coletor experimental do Diário Oficial de São Paulo, revisão 0.2.

Aceita documento JSON único ou sequência de documentos JSON bem formados.
Nunca registra conteúdo inválido como edição coletada. Apenas metadados verificáveis.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

SITE = 'https://diariooficial.prefeitura.sp.gov.br'
LISTAGEM = SITE + '/md_epubli_controlador.php?acao=diario_aberto&formato=A'
ARQUIVO = Path(__file__).resolve().parents[1] / 'dados' / 'edicoes_coletadas.json'
MAX_BYTES = 32 * 1024 * 1024


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            for k, v in attrs:
                if k.lower() == 'href' and v:
                    self.hrefs.append(v)


def url_json_oficial(html: str, base: str = LISTAGEM) -> str:
    parser = Links()
    parser.feed(html)
    for href in parser.hrefs:
        url = urljoin(base, href)
        u = urlparse(url)
        if (u.scheme != 'https' or u.netloc != 'diariooficial.prefeitura.sp.gov.br'
                or u.path != '/md_epubli_controlador.php'):
            continue
        qs = parse_qs(u.query)
        if (qs.get('acao') == ['edicao_download']
                and [s.lower() for s in qs.get('formato', [])] == ['json']
                and qs.get('dta') and qs.get('numEdicao')):
            datetime.strptime(qs['dta'][0], '%d/%m/%Y')
            return url
    raise RuntimeError('Não encontrei link de download JSON identificável na fonte oficial; nenhuma coleta será declarada.')


def baixar(url: str) -> bytes:
    req = Request(url, headers={'User-Agent': 'RadarSP-Pesquisa-Academica/0.1 (contato via GitHub)', 'Accept': 'text/html,application/json,*/*'})
    with urlopen(req, timeout=30) as res:
        tamanho = res.headers.get('Content-Length')
        if tamanho and int(tamanho) > MAX_BYTES:
            raise RuntimeError('Arquivo oficial excede tamanho máximo da coleta experimental')
        conteudo = res.read(MAX_BYTES + 1)
    if len(conteudo) > MAX_BYTES:
        raise RuntimeError('Arquivo oficial excede tamanho máximo da coleta experimental')
    return conteudo



def validar_formato_json(conteudo: bytes) -> tuple[str, int]:
    """Valida 1 JSON ou vários JSONs concatenados (sem descartar sufixos inválidos).

    Alguns sistemas exportam registros consecutivos em vez de um array JSON.
    Cada bloco precisa ser JSON válido e estrutural (objeto/lista). Nunca se
    aceita HTML, mensagens de erro ou texto residual como conteúdo válido.
    """
    try:
        texto = conteudo.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise RuntimeError('Download não é texto UTF-8 válido') from exc

    if not texto.strip():
        raise RuntimeError('Download sem conteúdo JSON')

    decodificador = json.JSONDecoder()
    pos = 0
    n = len(texto)
    blocos = 0
    while pos < n:
        while pos < n and texto[pos].isspace():
            pos += 1
        if pos >= n:
            break
        # Separador opcional entre objetos de exports que não usam um array.
        if blocos and texto[pos] == ',':
            pos += 1
            while pos < n and texto[pos].isspace():
                pos += 1
            if pos == n:
                raise RuntimeError('Download termina em vírgula solta após JSON')
        inicio = pos
        try:
            item, pos = decodificador.raw_decode(texto, pos)
        except json.JSONDecodeError as exc:
            proximo = repr(texto[inicio:inicio + 32])
            raise RuntimeError(
                f'Conteúdo não é JSON integralmente válido; bloco {blocos + 1}, '
                f'posição {inicio} de {n}, início do trecho: {proximo}. '
                'Não registrar edição. É necessário investigar o formato da origem.'
            ) from exc
        if not isinstance(item, (dict, list)):
            raise RuntimeError(f'Bloco {blocos + 1} contém JSON escalar, inesperado para edição.')
        blocos += 1
        if blocos > 10000:
            raise RuntimeError('Quantidade excessiva de blocos JSON; revisar exportação')
    if not blocos:
        raise RuntimeError('Download sem documentos JSON estruturais')
    return ('json_unico' if blocos == 1 else 'sequencia_json'), blocos


def main():
    html = baixar(LISTAGEM).decode('utf-8', errors='replace')
    url = url_json_oficial(html)
    conteudo = baixar(url)
    formato_json, quantidade_blocos = validar_formato_json(conteudo)
    print('Formato validado:', formato_json, '| Blocos JSON:', quantidade_blocos)

    params = parse_qs(urlparse(url).query)
    data = datetime.strptime(params['dta'][0], '%d/%m/%Y').date().isoformat()
    registro = {
        'municipio_ibge': '3550308',
        'data_edicao': data,
        'numero_edicao_interno': params['numEdicao'][0],
        'url_fonte_listagem': LISTAGEM,
        'url_download_json': url,
        'sha256_conteudo': hashlib.sha256(conteudo).hexdigest(),
        'bytes_conteudo': len(conteudo),
        'formato_json_validado': formato_json,
        'blocos_json_validados': quantidade_blocos,
        'data_coleta_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'tipo_registro': 'metadados_edicao_oficial',
        'atos_extraidos': False,
        'analise_de_exclusao_digital': 'nao_realizada',
        'validacao_cientifica': 'pendente',
    }
    ARQUIVO.parent.mkdir(exist_ok=True, parents=True)
    atuais = json.loads(ARQUIVO.read_text(encoding='utf-8')) if ARQUIVO.exists() else []
    for item in atuais:
        if item['data_edicao'] == data and item['numero_edicao_interno'] == registro['numero_edicao_interno'] and item['sha256_conteudo'] == registro['sha256_conteudo']:
            print('Edição já registrada e inalterada:', data)
            return
    atuais.append(registro)
    atuais = sorted(atuais, key=lambda x: (x['data_edicao'], x['numero_edicao_interno']))[-40:]
    ARQUIVO.write_text(json.dumps(atuais, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Metadados oficiais registrados:', data, 'edição:', registro['numero_edicao_interno'], 'bytes:', len(conteudo))
    print('AVISO: nenhum ato ou risco de exclusão digital foi classificado nesta etapa.')


if __name__ == '__main__':
    main()
