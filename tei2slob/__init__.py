# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License <http://www.gnu.org/licenses/gpl-3.0.txt>
# for more details.
#
# Copyright (C) 2015  Igor Tkach

import argparse
import collections
import functools
import logging
import os
import re
import sys
import urllib

from copy import deepcopy
from itertools import combinations
from xml.etree import ElementTree as etree

import slob

ARTICLE_CONTENT_TYPE = 'text/html;charset=utf-8'

Tag = collections.namedtuple('Tag', 'name value')
Content = collections.namedtuple('Content', 'text keys type')


NS = 'http://www.tei-c.org/ns/1.0'

NS_MAP = {'': NS, 't': NS}


def ns(name):
    return '{%s}%s' % (NS, name)


def strip_ns(name):
    return name.split('}')[1]


TAG_HEADER = ns('teiHeader')

TAG_ENTRY = ns('entry')

TAG_INCLUDE = '{http://www.w3.org/2001/XInclude}include'


def text(parent, path):
    element = parent.find(path, NS_MAP)
    if element is not None and element.text:
        return element.text
    return ''


def attr(parent, path, attr_name):
    element = parent.find(path, NS_MAP)
    return element.attrib.get(attr_name, '') if element is not None else ''


def normalize_ws(s):
    return re.sub(r'\s+', ' ', s)


class TEI:

    TITLE = './t:fileDesc/t:titleStmt/t:title'
    EDITION = './t:fileDesc/t:editionStmt/t:edition'
    PUB_PLACE_REF = './t:fileDesc/t:publicationStmt/t:pubPlace/*[@target]'
    ORIGIN_REF = './t:fileDesc/t:sourceDesc//*[@target]'
    LICENSE_REF = './t:fileDesc/t:publicationStmt/t:availability/t:p/*[@target]'
    COPYRIGHT = './t:fileDesc/t:publicationStmt/t:availability/t:p'

    def __init__(self, input_file):
        self.input_file = input_file

    def _parse_header(self, element):

        t = lambda path: text(element, path)
        a = lambda path, attr_name: attr(element, path, attr_name)

        yield Tag('label', t(TEI.TITLE))
        yield Tag('edition', t(TEI.EDITION))

        source = a(TEI.PUB_PLACE_REF, 'target')
        yield Tag('source', source)

        origin = a(TEI.ORIGIN_REF, 'target')
        yield Tag('origin', origin)

        noext = basename_notext(self.input_file)
        uri = noext

        if source:
            uri = source.rstrip('/') + '/' + noext

        yield Tag('uri', uri)

        yield Tag('license.name', normalize_ws(t(TEI.LICENSE_REF)))
        yield Tag('license.url', a(TEI.LICENSE_REF, 'target'))
        yield Tag('copyright', normalize_ws(t(TEI.COPYRIGHT)))


    def _parse_entry(self, element):
        orths = element.findall('./t:form//t:orth', NS_MAP)
        titles = [orth.text for orth in orths if orth.text]
        root = self.mk_entry_root()
        body = etree.SubElement(root, 'body')
        self.mk_html_element(element, body)
        txt = etree.tostring(root, encoding='utf-8', method='html')
        yield Content(txt, titles, ARTICLE_CONTENT_TYPE)

    def mk_entry_root(self):
        root = etree.Element('html')
        head = etree.SubElement(root, 'head')
        etree.SubElement(root, 'script', {'src': '~/js/styleswitcher.js'})

        etree.SubElement(head, 'link', {
            'rel': 'stylesheet',
            'type': 'text/css',
            'href': '~/css/default.css'
        })

        etree.SubElement(head, 'link', {
            'rel': 'alternate stylesheet',
            'type': 'text/css',
            'title': 'Night',
            'href': '~/css/night.css'
        })

        return root

    def mk_html_element(self, element, html_parent):
        lists = {}
        for child in element:
            tag = strip_ns(child.tag)
            classes = [tag]
            type_attr = child.attrib.get('type')
            if type_attr:
                classes.append(type_attr)
            attribs = {'class': ' '.join(classes).strip()}

            if tag in ('sense', 'cit', 'quote'):
                list_parent = lists.get(tag)
                if list_parent is None:
                    list_parent = etree.SubElement(html_parent, 'ol', {'class': tag})
                    lists[tag] = list_parent
                html_element = etree.SubElement(list_parent, 'li', attribs)
            else:
                html_element = etree.SubElement(html_parent, 'div', attribs)
            html_element.text = child.text
            self.mk_html_element(child, html_element)

        for ol in lists.values():
            if len(ol) == 1:
                ol.attrib['class'] += ' single'

    def __iter__(self):
        todo_files = [self.input_file]
        done_files = set()

        while todo_files:
            current_file = os.path.normpath(todo_files.pop())
            done_files.add(current_file)

            for _, element in etree.iterparse(current_file):

                if element.tag == TAG_HEADER:
                    yield from self._parse_header(element)
                    element.clear()

                if element.tag == TAG_ENTRY:
                    yield from self._parse_entry(element)
                    element.clear()

                if element.tag == TAG_INCLUDE:
                    include_file = os.path.join(
                            os.path.dirname(self.input_file),
                            element.attrib['href'])
                    if include_file in done_files:
                        raise Exception('{} is included multiple times. '
                                'Stopping to avoid infinite loop due to '
                                'circular includes.'.format(include_file))
                    todo_files.append(include_file)


def parse_args():

    arg_parser = argparse.ArgumentParser()

    arg_parser.add_argument('input_file', type=str,
                            help='TEI file name')

    arg_parser.add_argument('-o', '--output-file', type=str,
                            dest="output_file",
                            help='Name of output slob file')

    arg_parser.add_argument('-c', '--compression',
                            choices=['lzma2', 'zlib'],
                            default='zlib',
                            help='Name of compression to use. Default: %(default)s')

    arg_parser.add_argument('-b', '--bin-size',
                            type=int,
                            default=256,
                            help=('Minimum storage bin size in kilobytes. '
                                  'Default: %(default)s'))

    arg_parser.add_argument('-a', '--created-by', type=str,
                            default='',
                            help=('Value for created.by tag. '
                                  'Identifier (e.g. name or email) '
                                  'for slob file creator'))

    arg_parser.add_argument('-w', '--work-dir', type=str, default='.',
                            help=('Directory for temporary files '
                                  'created during compilation. '
                                  'Default: %(default)s'))

    return arg_parser.parse_args()


def basename_notext(path):
    basename = os.path.basename(path)
    noext = basename
    while True:
        noext, _ext = os.path.splitext(noext)
        if not _ext:
            return noext


def main():

    logging.basicConfig()

    observer = slob.SimpleTimingObserver()

    args = parse_args()

    outname = args.output_file

    if outname is None:
        noext = basename_notext(args.input_file)
        outname = os.path.extsep.join((noext, 'slob'))

    def p(s):
        sys.stdout.write(s)
        sys.stdout.flush()

    with slob.create(outname,
                     compression=args.compression,
                     workdir=args.work_dir,
                     min_bin_size=args.bin_size*1024,
                     observer=observer) as slb:
        observer.begin('all')
        observer.begin('content')
        #create tags
        slb.tag('label', '')
        slb.tag('license.name', '')
        slb.tag('license.url', '')
        slb.tag('source', os.path.basename(args.input_file))
        slb.tag('uri', '')
        slb.tag('copyright', '')
        slb.tag('created.by', args.created_by)

        input_file = os.path.expanduser(args.input_file)
        tei = TEI(input_file)
        content_dir = os.path.dirname(__file__)
        slob.add_dir(slb, content_dir,
                     include_only={'js', 'css'},
                     prefix='~/')
        print('Adding content...')
        for i, item in enumerate(tei):
            if i % 100 == 0 and i: p('.')
            if i % 5000 == 0 and i: p(' {}\n'.format(i))
            if isinstance(item, Tag):
                slb.tag(item.name, item.value)
            else:
                slb.add(item.text, *item.keys, content_type=item.type)

    edition = None
    with slob.open(outname) as s:
        edition = s.tags.get('edition')

    if edition and (not edition in outname or not args.output_file):
        noext, ext = os.path.splitext(outname)
        newname = '{noext}-{edition}{ext}'.format(noext=noext, edition=edition, ext=ext)
        os.rename(outname, newname)

    print('\nAll done in %s\n' % observer.end('all'))
