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
import sys
import urllib

from copy import deepcopy
from itertools import combinations
from xml.etree import ElementTree as etree

import slob

ARTICLE_CONTENT_TYPE = 'text/html;charset=utf-8'

ARTICLE_TEMPLATE = (
    '<script src="~/js/styleswitcher.js"></script>'
    '<link rel="stylesheet" href="~/css/default.css" type="text/css">'
    '<link rel="alternate stylesheet" href="~/css/night.css" type="text/css" title="Night">'
    '%s'
)

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

class TEI:

    def __init__(self, input_file):
        self.input_file = input_file

    def _parse_header(self, element):
        title = element.find('./t:fileDesc/t:titleStmt/t:title', NS_MAP).text
        yield Tag('label', title)

    def _parse_entry(self, element):
        orths = element.findall('./t:form/t:orth', NS_MAP)
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
        for _, element in etree.iterparse(self.input_file):

            if element.tag == TAG_HEADER:
                yield from self._parse_header(element)
                element.clear()

            if element.tag == TAG_ENTRY:
                yield from self._parse_entry(element)
                element.clear()



def parse_args():

    arg_parser = argparse.ArgumentParser()

    arg_parser.add_argument('input_file', type=str,
                            help='TEI file name')

    arg_parser.add_argument('-o', '--output-file', type=str,
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


def main():

    logging.basicConfig()

    observer = slob.SimpleTimingObserver()

    args = parse_args()

    outname = args.output_file

    basename = os.path.basename(args.input_file)

    noext = basename

    if outname is None:
        while True:
            noext, _ext = os.path.splitext(noext)
            if not _ext:
                break
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
        slb.tag('source', basename)
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

    print('\nAll done in %s\n' % observer.end('all'))
