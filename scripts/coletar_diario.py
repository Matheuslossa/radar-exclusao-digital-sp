#!/usr/bin/env python3
"""Coletor experimental da edição mais recente do Diário Oficial da Cidade de São Paulo.

Descobre o endereço JSON publicado no site oficial e grava somente metadados
verificáveis. Não classifica normas, não atribui riscos nem declara cobertura total.
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


def main():
    html = baixar(LISTAGEM).decode('utf-8', errors='replace')
    url = url_json_oficial(html)
    conteudo = baixar(url)
    # Não afirmar que o download é um JSON utilizável quando o servidor responder
    # com HTML, erro ou outro formato apesar da extensão.
    try:
        json.loads(conteudo.decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError('O arquivo oficial não pôde ser validado como JSON. Verificar o formato antes de continuar.') from exc

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
