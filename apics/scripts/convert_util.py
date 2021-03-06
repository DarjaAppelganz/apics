# coding=utf8
from io import open
from subprocess import call
import logging
import re
from hashlib import md5
from collections import defaultdict

from bs4 import BeautifulSoup
import cssutils
from nameparser import HumanName
from souplib import tag_and_text, new_tag, append, normalize_whitespace, text

from clld.util import slug, jsondump
from clld.db.meta import DBSession
from clld.db.models.common import Contributor, Language, Source, Sentence
from clld.lib.bibtex import Database, Record


NAME = '[A-Z][a-zê]'.decode('utf8')
YEAR = re.compile('(\.|(?P<ed>\(ed(s?)\.\)(,?)))\s*(?P<year>((\[[0-9]+(,\s*[0-9]+)*\]\s+)?([0-9]{4}(–|/))?[0-9]{4}[a-z\+]?)|(n\.d))\.'.decode('utf8'))
EDS = re.compile('In\s+(?P<eds>[^\(]+)\(eds?\.\),?\s*')
BTITLE_PAGES = re.compile('(?P<btitle>.+?),\s*(?P<pages>[0-9]+–[0-9]+)\.?\s*'.decode('utf8'))
PUBLISHER = re.compile('(?P<place>[^:]+):\s*(?P<publisher>([A-Z]\.)?[^\.]*)\.\s*')
JOURNAL = re.compile('(?P<journal>.+?)(?P<volume>[0-9]+)\s*(\((?P<number>[0-9]+)\))?\.\s*(?P<pages>[0-9]+–[0-9]+)\.?\s*'.decode('utf8'))

SURVEY_SECTIONS = [
    'Historical background',
    'Sociohistorical background',
    'Verb phrase',
    'Verb phrase and related categories',
    'Noun phrase',
    'The noun phrase',
    'Sentences',
    'Simple sentences',
    'Sociolinguistic situation',
    'Sociolinguistic background',
    'Introduction',
    'Phonology',
    'Complex sentences',
    'Complex sentences and their introducers',
    'Other features',
    'The lexicon',
    'The quotative particle',
    'Lexicon',
    'Genders',
    'Morphology',
    'Interrogative constructions',
    'Focus constructions',
    'Interrogative and focus constructions',
    'Interrogative sentences and focus constructions',
    'References',
    'Glossed Text',
    'Glossed text',
    'Glossed texts',
    'Acknowledgements',
    'Acknowledgement',
    'References and further reading',
    'Sources of examples',
    'Verb complex',
    'Adverbs',
    'Vowels',
    'Consonants',
    'Suprasegmentals',
    'Pronouns',
    'The relative pronoun ki',
    'Nouns',
    'Adjectives',
    'Preverbs, adverbs, and prepositions',
]

REFERENCE_CATEGORIES = [
    'Text',
    'Texts',
    'Grammatical description',
    'Grammatical descriptions',
    'Text/corpus',
    'Texts/corpora',
    'Texts/Corpora',
    'Grammar',
    'Grammars',
    'Dictionary',
    'Dictionaries',
    'Other',
    'Linguistic atlas',
    'Teaching manuals',
    'Further references',
    'Further reading',
    'History',
    'Special topics',
    'Grammars and dictionaries',
    'Articles and books',
    'Books and articles',
    'Dictionaries and handbooks',
    'Grammars and surveys',
    'Specific topics in grammar',
    'Berbice Dutch origins',
    'Other references cited',
    'Topics in Grammar',
    'Grammars and sketches',
]


def convert_chapter(fname, outdir):
    return
    call('unoconv -f html -o %s "%s"' % (outdir, fname), shell=True)
    out = outdir.joinpath(fname.basename().splitext()[0] + '.html')
    lines = []
    with open(out, encoding='utf8') as fp:
        for line in fp:
            if '<sdfield' not in line:
                lines.append(line)
    with open(out, 'w', encoding='utf8') as fp:
        fp.write('\n'.join(lines))
    call('tidy -q -m -c -utf8 -asxhtml %s' % out, shell=True)


class Parser(object):
    def __init__(self, fname):
        self.fname = fname
        self.authors = [c.id for c in DBSession.query(Contributor)]
        self.languages = {l.id: l.name for l in DBSession.query(Language)}
        self.id = self.get_id(fname)
        self.refs = {slug(s.name): s for s in DBSession.query(Source) if s.name}
        self.examples = defaultdict(list)
        for row in DBSession.query(Sentence):
            if row.description:
                self.examples[slug(row.description.split('OR:')[0])].append(
                    (row.name, row.id))
        for k in self.examples.keys():
            self.examples[k] = {slug(k): v for k, v in self.examples[k]}

    def __call__(self, outdir):
        """
        runs a parser workflow consisting of
        - preprocess
        - refactor
        - postprocess
        writes the results, an html, a css and a json file to disk.
        """
        cssutils_logger = logging.getLogger('CSSUTILS')
        cssutils_logger.setLevel(logging.ERROR)
        print(self.fname.namebase.encode('utf8'))

        with open(self.fname, encoding='utf8') as fp:
            c = fp.read()
        soup = BeautifulSoup(self.preprocess(self._preprocess(c)))

        # extract css from the head section of the HTML doc:
        css = cssutils.parseString('\n')
        for style in soup.find('head').find_all('style'):
            for rule in self.cssrules(style):
                css.add(rule)

        md = dict(outline=[], refs=[], authors=[])
        soup = self.refactor(soup, md)

        # enhance section headings:
        for section, t in tag_and_text(soup.find_all('h3')):
            t = t.split('[Note')[0]
            id_ = 'section-%s' % slug(t)
            md['outline'].append((t, id_))
            section.attrs['id'] = id_
            for s, attrs in [
                (u'\u21eb', {'href': '#top', 'title': 'go to top of the page', 'style': 'vertical-align: bottom'}),
                ('¶', {'class': 'headerlink', 'href': '#' + id_, 'title': 'Permalink to this section'}),
            ]:
                append(section, soup.new_string('\n'), new_tag(soup, 'a', s, **attrs))

        body = self.insert_links(unicode(soup.find('body')), md)

        # write output files:
        with open(outdir.joinpath('%s.html' % self.id), 'w', encoding='utf8') as fp:
            fp.write(self.wrap(self.postprocess(body)))

        with open(outdir.joinpath('%s.css' % self.id), 'wb') as fp:
            fp.write(self.csstext(css))

        md['authors'] = list(self.yield_valid_authors(md['authors']))
        jsondump(md, outdir.joinpath('%s.json' % self.id), indent=4)

    def yield_valid_authors(self, authors):
        for name in authors:
            n = HumanName(name)
            res = dict(name=name, id=slug(n.last + n.first + n.middle))
            if name == 'Margot C. van den Berg':
                res['id'] = 'vandenbergmargotc'
            if name == 'Khin Khin Aye':
                res['id'] = 'khinkhinayenone'
            if name == 'Melanie Halpap':
                res['id'] = 'revismelanie'
            if res['id'] not in self.authors:
                raise ValueError(name)
            yield res

    def get_ref(self, e, category=None):
        for f in e.find_all('font'):
            f.unwrap()
        t = text(e)
        ref = self.refs.get(slug(t))
        if ref:
            return dict(
                key=ref.name,
                id=slug(t),
                text='%s. %s.' % (ref.name, ref.description),
                html=u'<a href="/sources/{0.id}">{0.name}</a>. {0.description}.'.format(ref),
                category=category)
        match = YEAR.search(t)
        if match:
            authors = t[:match.start()].split('(')[0].strip()
            authors = [HumanName(n.strip()).last for n in authors.split('&')]
            key = '%s %s' % (' & '.join(authors), match.group('year').strip())
        else:
            key = None
        return dict(
            key=key,
            id=slug(key) if key else unicode(md5(t.encode('utf8')).hexdigest()),
            text=t,
            html=unicode(e),
            category=category)

    def insert_links(self, html, md):
        end_tag = re.compile('[^<]*</a>')

        def repl(match):
            if end_tag.match(match.string[match.end():]):
                # if the next tag is the end tag of a link, then don't link again!
                return match.string[match.start():match.end()]
            return '<a class="ref-link" style="cursor: pointer;" data-content="%s">%s</a>' \
                   % (slug(match.group('key').replace('&amp;', '&')), match.group('key'))

        ids = {}
        for ref in sorted(md['refs'], key=lambda r: len(r.get('key') or ''), reverse=True):
            if ref['key']:
                ids[ref['id']] = 1
                html = re.sub('(?P<key>' + ref['key'].replace(' ', '\s+\(?').replace('&', '&amp;') + ')', repl, html, flags=re.M)

        def repl2(match):
            s = match.string[match.start():match.end()]
            id_ = slug(match.group('key').replace('&amp;', '&'))
            ref = self.refs.get(id_)
            if not ref or id_ in ids:
                return s
            return '%s<a href="/sources/%s">%s</a>%s' \
                   % (match.group('before'), ref.id, match.group('key'), match.group('after'))

        html = re.sub('(?P<before>\(|>)(?P<key>' + NAME + '\s+(&\s+' + NAME + '\s+)*[0-9]{4})(?P<after><|\)|:)', repl2, html, flags=re.M)

        section_lookup = {}
        for t, id_ in md['outline']:
            m = re.match('(?P<no>[0-9]+(\.[0-9]+)?)\.\s+', t)
            if m:
                section_lookup[m.group('no')] = id_

        def repl3(match):
            if end_tag.match(match.string[match.end():]):
                # if the next tag is the end tag of a link, then don't link again!
                return match.string[match.start():match.end()]
            return '<a class="section-link" href="#%s">§%s</a>'.decode('utf8') \
                   % (section_lookup[match.group('no')], match.group('no'))

        for no in section_lookup:
            html = re.sub('§(</span><span>)(?P<no>'.decode('utf8') + no.replace('.', '\\.') + ')', repl3, html, flags=re.M)

        lookup = {v: k for k, v in self.languages.items()}

        def langs(match):
            if end_tag.match(match.string[match.end():]):
                # if the next tag is the end tag of a link, then don't link again!
                return match.string[match.start():match.end()]
            name = normalize_whitespace(match.group('name'))
            return '<a href="/contributions/%s">%s</a>%s' % (lookup[name], name, match.group('s'))

        for name in sorted(lookup.keys(), key=lambda n: len(n), reverse=True):
            html = re.sub('(?P<name>' + name.replace(' ', '\s+') + ')(?P<s>[^a-z])', langs, html, flags=re.M)
        return html

    def _preprocess(self, html):
        html = re.sub('<!\[if\s+[^\]]+\]>', '', html)
        html = re.sub('<!\[endif\]>', '', html)
        parts = []
        for i, p in enumerate(html.split('<!--[if')):
            if i == 0:
                parts.append(p)
            else:
                parts.append(p.split('endif]-->')[1])
        return ''.join(parts)

    def preprocess(self, html):
        return html

    def postprocess(self, html):
        new = []
        pos = 0
        _number = 0
        soup = BeautifulSoup()

        def popover(number, note):
            # we use BeautifulSoup to fix broken markup, e.g. incomplete span tags.
            note = BeautifulSoup(normalize_whitespace(note)).find('body')
            note.name = 'div'
            a = new_tag(
                soup,
                'a',
                new_tag(soup, 'sup', number),
                **{
                    'style': 'text-decoration: underline; cursor: pointer;',
                    'class': 'popover-note',
                    'data-original-title': 'Note %s' % number,
                    'data-content': unicode(note),
                    })
            return unicode(a)

        for match in re.finditer('\[Note\s+(?P<number>[0-9]+):\s*', html, flags=re.M):
            new.append(html[pos:match.start()])
            level = 0
            for i, c in enumerate(html[match.end():]):
                if c == '[':
                    level += 1
                if c == ']':
                    if level == 0:
                        closing_bracket = match.end() + i
                        break
                    level -= 1
            else:
                raise ValueError(match.group('number'))
            _number += 1
            if int(match.group('number')) != _number:
                print '--- missing note %s' % _number
            _number = int(match.group('number'))

            new.append(popover(match.group('number'), html[match.end():closing_bracket]))
            pos = closing_bracket + 1

        new.append(html[pos:])
        return ''.join(new)

    def wrap(self, html):
        html = re.sub(' (xml:)?lang="[a-z]{2}\-[A-Z]{2}"', '', html).replace('</body>', '')
        return """\
<!DOCTYPE html>
<html>
    <head>
        <meta charset="utf-8">
        <link rel="stylesheet" type="text/css" href="%s.css">
        <script src="http://code.jquery.com/jquery.js"></script>
        <link href="http://maxcdn.bootstrapcdn.com/twitter-bootstrap/2.3.2/css/bootstrap-combined.min.css" rel="stylesheet">
        <script src="http://maxcdn.bootstrapcdn.com/twitter-bootstrap/2.3.2/js/bootstrap.min.js"></script>
    </head>
    %s
        <script>
            $(document).ready(function(){
                $('.popover-note').popover({html: true});
            });
        </script>
    </body>
</html>
    """ % (self.id, html)

    def get_id(self, fname):
        return fname.namebase

    def refactor(self, soup, md):
        return soup

    def cssrules(self, style):
        res = text(style)
        for s in ['/*<![CDATA[*/', '<!--', '/*]]>*/', '-->']:
            res = res.replace(s, '')
        res = re.sub('/\*[^\*]+\*/', '', res)
        res = re.sub('mso\-[^\:]+\:[^;]+;', '', res)
        for rule in [r.strip() + '}' for r in res.strip().split('}') if r]:
            selector = rule.split('{')[0]
            if not rule.startswith('@page') \
                    and ':' not in selector \
                    and not re.search('\.[0-9]+', selector):
                yield rule

    def csstext(self, css):
        lines = []
        for line in css.cssText.split('\n'):
            if ':' in line:
                selector, rule = line.strip().split(':', 1)
                if selector in [
                    'font-family',
                    'line-height',
                    'font-size',
                    'so-language',
                    'margin-left',
                    'margin-right',
                    'direction',
                    'color',
                ]:
                    continue
                if 'mso-' in rule:
                    continue
            lines.append(line)
        css = cssutils.parseString('\n'.join(lines))
        return css.cssText


def normalized_author(s):
    def format_name(name):
        name.string_format = u"{last}, {title} {first} {middle}, {suffix}"
        return unicode(name)

    authors = []
    for part in re.split(',?\s+(?:&|and|with)\s+', s.strip()):
        subparts = re.split('\s*,\s*', part)
        n = len(subparts)
        if n == 1:
            authors.append(HumanName(subparts[0]))
        elif n == 2:
            authors.append(HumanName(', '.join(subparts)))
        else:
            try:
                assert len(subparts) % 2 == 0
            except:
                print '~~~~', s
                return '', s
            for i in range(0, len(subparts), 2):
                authors.append(HumanName(', '.join(subparts[i:i+2])))
    if len(authors) == 1:
        key = authors[0].last
    elif len(authors) == 2:
        key = ' & '.join(a.last for a in authors)
    else:
        key = '%s et al.' % authors[0].last
    return key, u' and '.join(map(format_name, authors))


def _get_bibtex(refs):
    for ref in refs:
        genre = 'misc'
        id = ref['id']
        attrs = dict(all=ref['text'])
        t = ref['text']
        match = YEAR.search(t)
        if match:
            authors = 'editor' if match.group('ed') else 'author'
            attrs['key'], attrs[authors] = normalized_author(t[:match.start()].strip())
            attrs['title'], rem = [s.strip() for s in re.split('\.|\?', t[match.end():], 1)]
            attrs['year'] = match.group('year')
            attrs['key'] = '%(key)s %(year)s' % attrs
            m = EDS.match(rem)
            if m:
                assert 'editor' not in attrs
                attrs['editor'] = normalized_author(m.group('eds').strip())[1]
                genre = 'incollection'
                rem = rem[m.end():].strip()
                mm = BTITLE_PAGES.match(rem)
                if mm:
                    attrs['booktitle'] = mm.group('btitle').strip()
                    attrs['pages'] = mm.group('pages').strip()
                    rem = rem[mm.end():].strip()
            else:
                mm = JOURNAL.match(rem)
                if mm:
                    genre = 'article'
                    attrs['journal'] = mm.group('journal').strip()
                    attrs['volume'] = mm.group('volume').strip()
                    if mm.group('number'):
                        attrs['number'] = mm.group('number').strip()
                    attrs['pages'] = mm.group('pages').strip()
                    rem = rem[mm.end():].strip()
            m = PUBLISHER.match(rem)
            if m:
                if genre == 'misc':
                    genre = 'book'
                attrs['place'] = m.group('place').strip()
                attrs['publisher'] = m.group('publisher').strip()
                rem = rem[m.end():].strip()
            _rem = []
            for piece in [p.strip() for p in re.split('\.(?:\s+|$)', rem) if p.strip()]:
                if piece.startswith('http') and not re.search('\s+', piece):
                    attrs['url'] = piece
                elif piece.startswith('(') and piece.endswith(')'):
                    attrs['note'] = piece[1:-1].strip()
                else:
                    _rem.append(piece)
            rem = '. '.join(_rem)
            if not slug(unicode(rem)):
                del attrs['all']
        yield Record(genre, id, **attrs)


def get_bibtex(refs):
    return Database(list(_get_bibtex(refs)))
