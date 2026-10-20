#!/usr/bin/env python3
"""RADAR São Paulo | Coletor experimental 0.3.

Lê a edição oficial em JSON (inclusive exportação com um ';' final),
valida o arquivo e registra apenas metadados verificáveis.
Não extrai atos, não avalia órgãos e não classifica exclusão digital.
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
            for chave, valor in attrs:
                if chave.lower() == 'href' and valor:
                    self.hrefs.append(valor)


def url_json_oficial(html: str, base: str = LISTAGEM) -> str:
    parser = Links()
    parser.feed(html)
    for href in parser.hrefs:
        url = urljoin(base, href)
        destino = urlparse(url)
        if (destino.scheme != 'https' or destino.netloc != 'diariooficial.prefeitura.sp.gov.br'
                or destino.path != '/md_epubli_controlador.php'):
            continue
        qs = parse_qs(destino.query)
        if (qs.get('acao') == ['edicao_download']
                and [s.lower() for s in qs.get('formato', [])] == ['json']
                and qs.get('dta') and qs.get('numEdicao')):
            datetime.strptime(qs['dta'][0], '%d/%m/%Y')
            return url
    raise RuntimeError('Nenhum link JSON identificado na fonte oficial: coleta não realizada.')


def baixar(url: str) -> bytes:
    req = Request(url, headers={
        'User-Agent': 'RadarSP-Pesquisa-Academica/0.3 (contato via GitHub)',
        'Accept': 'text/html,application/json,*/*'
    })
    with urlopen(req, timeout=30) as res:
        tamanho = res.headers.get('Content-Length')
        if tamanho and int(tamanho) > MAX_BYTES:
            raise RuntimeError('Download excede tamanho máximo do piloto')
        dados = res.read(MAX_BYTES + 1)
    if len(dados) > MAX_BYTES:
        raise RuntimeError('Download excede tamanho máximo do piloto')
    return dados


def validar_formato_json(conteudo: bytes) -> tuple[str, int]:
    """Valida objeto/lista JSON, sequência de JSONs ou exportação com ';' final.

    A fonte oficial pode acrescentar um único ponto e vírgula ao fim de um JSON.
    Essa exceção só é aceita depois da validação completa dos objetos JSON e se
    não houver nenhum outro caractere depois (além de espaço em branco).
    """
    try:
        texto = conteudo.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise RuntimeError('Conteúdo baixado não é UTF-8 válido') from exc
    if not texto.strip():
        raise RuntimeError('Conteúdo oficial vazio')
    decodificador = json.JSONDecoder()
    pos, blocos, sufixo = 0, 0, False
    tamanho = len(texto)
    while pos < tamanho:
        while pos < tamanho and texto[pos].isspace():
            pos += 1
        if pos == tamanho:
            break
        # Exportação real identificada na execução #2: JSON inteiro mais ';' final.
        if blocos and texto[pos] == ';' and not texto[pos + 1:].strip():
            sufixo = True
            pos = tamanho
            break
        # Algumas exportações concatenam blocos separados por vírgula.
        if blocos and texto[pos] == ',':
            pos += 1
            while pos < tamanho and texto[pos].isspace():
                pos += 1
            if pos >= tamanho:
                raise RuntimeError('Vírgula solta no final do arquivo')
        inicio = pos
        try:
            item, pos = decodificador.raw_decode(texto, pos)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f'Arquivo não é JSON integralmente válido; '
                f'bloco {blocos + 1}, posição {inicio} de {tamanho}, '
                f'trecho: {texto[inicio:inicio + 32]!r}. Nada será registrado.'
            ) from exc
        if not isinstance(item, (dict, list)):
            raise RuntimeError('Bloco JSON não é um objeto ou lista')
        blocos += 1
        if blocos > 10000:
            raise RuntimeError('Mais de 10000 blocos JSON: necessário investigar a fonte')
    if not blocos:
        raise RuntimeError('Nenhum objeto ou lista JSON detectado')
    nome = 'json_unico' if blocos == 1 else 'sequencia_json'
    if sufixo:
        nome += '_ponto_virgula_final'
    return nome, blocos


def main():
    html = baixar(LISTAGEM).decode('utf-8', errors='replace')
    url = url_json_oficial(html)
    conteudo = baixar(url)
    formato, quantidade = validar_formato_json(conteudo)
    print('Formato validado:', formato, '| Quantidade de blocos:', quantidade)

    parametros = parse_qs(urlparse(url).query)
    data = datetime.strptime(parametros['dta'][0], '%d/%m/%Y').date().isoformat()
    registro = {
        'municipio_ibge': '3550308',
        'data_edicao': data,
        'numero_edicao_interno': parametros['numEdicao'][0],
        'url_fonte_listagem': LISTAGEM,
        'url_download_json': url,
        'sha256_conteudo': hashlib.sha256(conteudo).hexdigest(),
        'bytes_conteudo': len(conteudo),
        'formato_json_validado': formato,
        'blocos_json_validados': quantidade,
        'data_coleta_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'tipo_registro': 'metadados_edicao_oficial',
        'atos_extraidos': False,
        'analise_de_exclusao_digital': 'nao_realizada',
        'validacao_cientifica': 'pendente',
    }
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    atuais = json.loads(ARQUIVO.read_text(encoding='utf-8')) if ARQUIVO.exists() else []
    for item in atuais:
        if (item['data_edicao'] == data
                and item['numero_edicao_interno'] == registro['numero_edicao_interno']
                and item['sha256_conteudo'] == registro['sha256_conteudo']):
            print('Edição idêntica já registrada:', data)
            return
    atuais.append(registro)
    atuais = sorted(atuais, key=lambda item: (item['data_edicao'], item['numero_edicao_interno']))[-40:]
    ARQUIVO.write_text(json.dumps(atuais, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Metadados oficiais registrados:', data, 'edição:', registro['numero_edicao_interno'])
    print('AVISO: não foram extraídos atos nem analisados riscos de exclusão digital.')


if __name__ == '__main__':
    main()
