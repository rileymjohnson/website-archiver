from selectolax.parser import HTMLParser, Node
import urllib.parse as url_parse
import css_parser as css_utils
from pathlib import Path
import hashlib
import logging
import base64
import string
import httpx
import json
import cgi

from typing import Tuple, Dict, Optional


html_node_template = '<html><body><{0}>{1}</{0}></body></html>'

def update_node(
    node: Node,
    *,
    tag: Optional[str]=None,
    contents: Optional[str]=None,
    attrs: Optional[Dict[str, str]]=None
) -> None:
    if tag is None:
        tag = node.tag

    if contents is None:
        contents = node.text()

    if attrs is None:
        attrs = node.attrs

    replacement_node = html_node_template.format(tag, contents)
    replacement_node = HTMLParser(replacement_node).body.child

    for attr, value in attrs.items():
        replacement_node.attrs[attr] = value

    node.replace_with(replacement_node)

def generate_url_hash(url: str) -> str:
    url_hash = hashlib \
        .sha1(url.encode()) \
        .hexdigest()

    return f'_{url_hash}'

def normalize_url(root_url: str, url: str) -> str:
    *url_pieces, _, _ = url_parse.urlsplit(
        url_parse.urljoin(root_url, url)
    )

    url = url_parse.urlunsplit((*url_pieces, '', ''))

    return url_parse.unquote(url)

class HTMLTemplate(string.Template):
    delimiter = '$$$'

asset_url_file_formats = {
    '.bmp', '.css', '.doc', '.docx', '.eot', '.gif',
    '.ico', '.jpeg', '.jpg', '.mp3', '.mp4', '.odt',
    '.ogg', '.otf', '.pdf', '.png', '.rtf', '.svg',
    '.tif', '.tiff', '.ttf', '.txt', '.wav', '.webm',
    '.webp', '.woff', '.woff2', '.xls', '.xlsb',
    '.xlsx', '.xml'
}

lookup_tag_attrs = {
    'image': 'xlink:href',
    'use': 'xlink:href',
    'object': 'data',
    'link': 'href'
}

lookup_css_query = ', '.join([
    'svg image[xlink\:href]',
    'svg use[xlink\:href]',
    'source[src]',
    'track[src]',
    'audio[src]',
    'video[src]',
    'embed[src]',
    'iframe[src]',
    'img[src]',
    'object[data]',
    'link[rel*="stylesheet"][href]'
])

class Archiver:
    def __init__(
        self,
        save_folder: Path | str,
        *,
        http_timeout: int=100,
        user_agent: str='Mozilla/5.0 (X11; Linux i686; rv:111.0) Gecko/20100101 Firefox/111.0'
    ):
        if isinstance(save_folder, str):
            self.container_folder = Path(save_folder)
        else:
            self.container_folder = save_folder

        if not self.container_folder.exists():
            self.container_folder.mkdir()

        self.assets_folder = self.container_folder.joinpath('assets')

        if not self.assets_folder.exists():
            self.assets_folder.mkdir()

        self.metadata_file = self.container_folder.joinpath('metadata.json')

        self.httpx_options = {
            'follow_redirects': True,
            'timeout': http_timeout,
            'headers': {
                'user-agent': user_agent
            }
        }

        self.initialize()

    def initialize(self):
        self.logger = logging.getLogger('CSS_PARSER')
        self.logger.setLevel(logging.CRITICAL)
        self.logger.disabled = True

        self.css_parser = css_utils.CSSParser(
            log=self.logger,
            loglevel=logging.CRITICAL,
            fetcher=lambda *a, **k: self._url_fetcher(*a, **k)
        )

        css_serializer_options = css_utils.serialize.Preferences()
        css_serializer_options.useMinified()

        css_serializer = css_utils.CSSSerializer(css_serializer_options)

        self.httpx_client = None

    def __enter__(self):
        self.httpx_client = httpx.Client(**self.httpx_options)
        return self

    def __exit__(self, *_):
        self.httpx_client = None

    def get_url_raw_html_file(self, url: str) -> Path:
        identifier = generate_url_hash(url)

        return self.container_folder \
            .joinpath(f'{identifier}.raw.html')

    def get_url_template_html_file(self, url: str) -> Path:
        identifier = generate_url_hash(url)

        return self.container_folder \
            .joinpath(f'{identifier}.template.html')

    def url_to_file(self, url: str) -> Path:
        url_hash = generate_url_hash(url)

        url_path = url_parse.urlsplit(url).path
        url_suffix = Path(url_path).suffix.lower()

        return self.assets_folder \
            .joinpath(url_hash) \
            .with_suffix(url_suffix)

    def template_id_to_file(self, template_id: str) -> Path:
        *file_stem_chunks, suffix = template_id.split('_')
        file_stem = '_'.join(file_stem_chunks)

        return self \
            .assets_folder \
            .joinpath(file_stem) \
            .with_suffix(f'.{suffix}')

    def _url_resolver(self, asset_url: str, asset_urls_metadata: Dict[str, Dict[str, str]]) -> str:
        asset_url = normalize_url(url, asset_url)
        asset_file = self.url_to_file(asset_url)

        if asset_file.suffix not in asset_url_file_formats:
            if asset_url.startswith('data:'):
                return asset_url
            else:
                fragment = url_parse.urlsplit(asset_url).fragment

                if fragment == '':
                    return asset_url
                else:
                    return f'#{fragment}'

        template_identifier = f'{asset_file.stem}_{asset_file.suffix[1:]}'

        if not asset_file.exists():
            encoding, content_type, text, content = self._url_fetcher(
                asset_url,
                return_bytes=True
            )

            asset_urls_metadata[template_identifier] = {
                'content_type': content_type,
                'url': asset_url
            }

            if asset_file.suffix == '.css':
                stylesheet = self.css_parser.parseString(
                    text,
                    encoding=encoding,
                    href=url
                )

                flat_stylesheet = css_utils.css.CSSStyleSheet()
                css_utils.resolveImports(stylesheet, flat_stylesheet)

                css_utils.replaceUrls(
                    flat_stylesheet,
                    lambda u: self._url_resolver(u, asset_urls_metadata),
                    ignoreImportRules=True
                )

                css = flat_stylesheet \
                    .cssText \
                    .decode(flat_stylesheet.encoding)

                asset_file.write_text(css, encoding=encoding)
            else:
                if content_type.startswith('text'):
                    asset_file.write_text(text, encoding=encoding)
                else:
                    asset_file.write_bytes(content)

        return f'$$${{{template_identifier}}}'

    def _url_fetcher(self, url: str, return_bytes=False) -> Tuple[str, str]:
        if self.httpx_client is None:
            response = httpx.get(url, **self.httpx_options)
        else:
            response = self.httpx_client.get(url)

        if return_bytes:
            content_type = response.headers.get('content-type', '')
            content_type, _ = cgi.parse_header(content_type)

            return (
                response.encoding,
                content_type,
                response.text,
                response.content
            )

        return response.encoding, response.text

    @classmethod
    def archive_simple(cls, url: str, save_folder: Path | str) -> None:
        archiver = cls(save_folder)
        archiver.archive_url(url)

    def archive_url(self, url: str) -> None:
        encoding, html = self._url_fetcher(url)

        html_file = self.get_url_raw_html_file(url)

        html_file.write_text(
            html,
            encoding=encoding
        )

        html = HTMLParser(html)

        asset_urls_metadata = {}

        # Look for any tag with inline styles
        for tag in html.css('*[style]'):
            tag_attr_css = tag.attrs.get('style')

            stylesheet = self.css_parser.parseStyle(
                tag_attr_css,
                encoding=encoding
            )

            css_utils.replaceUrls(
                stylesheet,
                lambda u: self._url_resolver(u, asset_urls_metadata),
                ignoreImportRules=True
            )

            tag.attrs['style'] = stylesheet.cssText

        # Look for tags that have asset attrs, such
        # as `<img />`, `<iframe />`, and `<video />`
        for tag in html.css(lookup_css_query):
            tag_attr = lookup_tag_attrs.get(tag.tag, 'src')
            tag_url = tag.attrs.get(tag_attr)

            template_id = self._url_resolver(tag_url, asset_urls_metadata)

            if tag.tag == 'link':
                tag.attrs['data-template-id'] = template_id[4:-1]
            else:
                tag.attrs[tag_attr] = template_id

        # Look for style tags
        for style_tag in html.css('style'):
            style_tag_css = style_tag.text(strip=True)

            stylesheet = self.css_parser.parseString(
                style_tag_css,
                encoding=encoding,
                href=url
            )

            flat_stylesheet = css_utils.css.CSSStyleSheet()
            css_utils.resolveImports(stylesheet, flat_stylesheet)

            css_utils.replaceUrls(
                flat_stylesheet,
                lambda u: self._url_resolver(u, asset_urls_metadata),
                ignoreImportRules=True
            )

            style_tag_css = flat_stylesheet \
                .cssText \
                .decode(flat_stylesheet.encoding)

            new_style_tag = HTMLParser(
                '<html><body><{0}>{1}</{0}></body></html>'.format(
                    style_tag.tag,
                    style_tag_css
                )
            ).body.child

            for attr, val in style_tag.attrs.items():
                new_style_tag.attrs[attr] = val

            style_tag.replace_with(new_style_tag)

        with open(self.metadata_file, 'w') as f:
            json.dump(
                asset_urls_metadata,
                f,
                sort_keys=True,
                indent=4
            )

        # Eliminate all script tags because they
        # will just throw a bunch of errors
        for script_tag in html.css('script'):
            script_tag.decompose()

        template_file = self.get_url_template_html_file(url)

        template_file.write_text(
            html.html,
            encoding=html.input_encoding
        )

    def get_template_identifiers(self, template: string.Template, metadata: Dict[str, Dict[str, str]]) -> Dict[str, str]:
        template_vars = {}

        for template_id in template.get_identifiers():
            id_metadata = metadata[template_id]
            content_type = id_metadata['content_type']

            if content_type == 'text/css':
                content = self \
                    .template_id_to_file(template_id) \
                    .read_text(encoding='utf-8')

                css_template = HTMLTemplate(content)

                css_str = css_template.substitute(
                    self.get_template_identifiers(css_template, metadata)
                )

                css_str = url_parse.quote(css_str)

                data_url = f'data:text/css;charset=UTF-8,{css_str}'
            else:
                content = self \
                    .template_id_to_file(template_id) \
                    .read_bytes()

                encoded_content = base64.b64encode(content).decode()

                data_url = f'data:{content_type};base64,{encoded_content}'

            template_vars[template_id] = data_url

        return template_vars

    def render_url_to_flat_file(self, url: str, save_file: Path | str) -> None:
        with open(self.metadata_file, 'r') as f:
            metadata = json.load(f)

        template_file = self.get_url_template_html_file(url)

        with open(template_file, 'r', encoding='utf-8') as f:
            template = HTMLTemplate(f.read())

        rendered_template = template.substitute(
            self.get_template_identifiers(template, metadata)
        )

        Path(save_file).write_text(
            rendered_template,
            encoding='utf-8'
        )
