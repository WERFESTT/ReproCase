#!/usr/bin/env python3
"""Сборка английской статьи. Сеть и вызовы модели не используются."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


TITLE = 'Reproducibility and Assertion Strength in ReproCase'
SUBTITLE = 'Two historical defects and six new model responses'
SECTIONS = [
('Abstract', [
    'A generated test can reproduce a known bug and pass after its fix while still accepting incorrect results. We examine this gap using ReproCase, where a language model selects an input domain and an assertion from a restricted schema, and a local program searches for a counterexample. Six new Nemotron responses produced 12 portable test bundles for two historical defects. All 24 executions before and after the fixes matched their expected outcomes. Handwritten assertions achieved the same confirmations with the same number of calls to the tested functions. Two incorrect outputs constructed after the experiment passed the model assertions but failed the handwritten assertions. In a separate series of 120 searches, narrowing the input domain prevented detection in the tested configurations, and increasing the search budget did not recover it. These results show a specific limitation of using regression reproduction as evidence of assertion strength.'
]),
('Introduction', [
    'Developer assistants can use generated tests to check proposed fixes. A test that fails before a known fix and passes after it provides evidence about that regression. It does not establish how well its assertion distinguishes other correct and incorrect outputs.',
    'We study this distinction with the previously published ReproCase tool [5]. The new experiments examine input domains and search budgets, collect six fresh model responses, and verify the exported tests. We then probe the accepted assertions with two constructed incorrect outputs. The study concerns assertion preparation within a constrained testing workflow. It does not evaluate repository navigation, autonomous patch generation, or model training.'
]),
('Method', [
    'The cases are two known defects: numerical instability in CPython correlation and failure when reversing an empty numeric range in more-itertools. We use pinned source versions before and after pull requests 107855 and 1153 [6, 7]. The original ReproCase revision is c8c7edc0a92a81183f1f8670871892cf9a4120f7.',
    'Each request supplies a symptom, a public behavioral contract, a human-written input generator, and a response schema. The model chooses subranges of the allowed inputs and an assertion from a fixed set of operations. It returns data rather than executable code. The request excludes the historical patch, a known counterexample, and local search results. The saved request bodies were checked against the pinned request builder.',
    'The program checks response structure and whether the assertion holds under the supplied contract. For correlation, the generator creates two symmetric, positively proportional pairs, whose mathematical correlation is one. For integer ranges, validation exhaustively checks the proposed finite domain against an independent reference operation. This rejects false properties but does not measure how many incorrect results a valid property would accept.',
    'Boundary search and Hypothesis each receive at most 252 calls for search and shrinking. Confirmation permits up to four additional calls, two before and two after the fix. If the budget expires, a found counterexample can be confirmed without completed shrinking. The logs distinguish those outcomes. The fixed implementation is used for confirmation, not to select the proposal or search input.',
    'The manual control uses the same permitted domains and search methods. Its correlation assertion requires a result within an absolute tolerance of 1e-12 of one. Its range assertion requires exact equality with the built-in integer range in reverse order. These are stronger handwritten assertions, not responses from another model.'
]),
('New model responses', [
    'We made six requests to nvidia/nemotron-3-super-120b-a12b through a remote API, three per case. Each session allowed one request without an automatic retry, at most 1024 completion tokens, and a serialized request body of at most 16000 bytes. Seeds 0, 1, and 2 control local search, not model generation. The recorded environment was Python 3.14.4 with Hypothesis 6.170.9.',
    'All six proposals passed structural and contract validation. For correlation, all three responses selected a finite result between -1 and 1 and exponents between -500 and 500. For reversed ranges, all three selected completion without an exception over the full allowed domain. The six responses therefore contain two distinct proposals.',
    'Each group of six confirmations in Table 1 combines two search methods across three sessions. The model and manual assertions detected the same historical defects. On the fixed versions, none of the 12 model searches or 12 manual searches found a counterexample within budget. This result does not establish that the fixed versions contain no other defects.',
    {'table': 'live'},
    'The 12 confirmed model searches produced 12 bundles for two defects. Model and manual conditions each used 3911 calls to the tested functions, including 48 confirmation calls. Initial export checks added 24 calls. We observed no advantage in detection or function-call count for the model on these cases. Time spent preparing the manual assertions was not measured.'
]),
('Input domains and search budgets', [
    'A separate local experiment reused earlier saved proposals without new model requests. Its question was chosen after reviewing the earlier pilot, and the protocol was fixed before this series ran. Five conditions were tested at budgets of 16, 64, and 252 calls. For each budget, boundary search ran once and Hypothesis used three seeds. Testing both historical versions produced 120 searches, 60 before the fixes and 60 after.',
    'This series used Python 3.12.3, Hypothesis 6.98.15, and sortedcontainers 2.4.0. Its results are kept separate from the new API series. The wide correlation domain uses exponents from -500 to 500; the narrow domain uses -10 to 10. The saved model assertion requires a finite value between -1 and 1. The manual assertion requires one. A manual reversed-range condition serves as a control.',
    {'table': 'offline'},
    'Each denominator of 12 combines three budgets with one boundary search and three Hypothesis runs per budget. All tested wide-domain searches confirmed the regression. No tested narrow-domain search did, including at the largest budget. This observation is specific to the generators and defects used here; it does not imply that wider domains are generally better.',
    'The accepted series produced 36 portable bundles. Their 72 separate executions matched the expected outcomes. An earlier attempt left a partial detailed log with 119 completed rows and one unstarted row, despite a full summary. That log was retained but excluded. After fixing the save operation, the complete series was repeated. The two attempts were not pooled.'
]),
('Assertion sensitivity', [
    'After examining the new responses, we constructed two incorrect outputs and passed them directly to the existing assertion evaluator. These controls were chosen after the experiment, not drawn from a predefined independent defect set. They did not modify the historical implementations or require model requests.',
    {'table': 'controls'},
    'Both conditions accepted the correct outputs for the same inputs: one for correlation and [4, 3, 2, 1, 0] for the reversed range. The manual assertion therefore did not reject the constructed errors merely by rejecting every value.',
    'The model assertions are valid but weaker than the manual controls. Completion without an exception does not check the returned sequence. Requiring correlation to lie between -1 and 1 accepts zero even when the generated pairs require one. These controls demonstrate specific omissions. They are not additional historical bugs and are not included in defect-detection rates.'
]),
('Reproducibility', [
    'The original audit matched the two saved copies of the live result and verified 165 text files against the pinned Git revision. Each live bundle contained 39 source files checked against that revision, plus a test checked against the export template. Each witness was linked to its proposal, case, method, and search seed.',
    'All 12 live tests were rerun before and after the fixes with isolated Python imports. All 24 outcomes matched: one assertion failure before each fix and a passing test after it, with no test-harness errors. This replay used Python 3.12.3. It establishes compatibility of these bundles with a second environment, not a repetition of model generation or search under Python 3.14.4.',
    'The shared source copy uses neutral API identifiers and omits connection addresses. Its replay bundles were repackaged with updated integrity metadata and checked separately. The assertions, domains, inputs, and historical implementations were preserved. The package identifies these files as derivatives of the original run rather than byte-identical originals.',
    'The client logs report 9282 prompt tokens, including 3040 cached prompt tokens, and 1571 completion tokens. Client records are not independent provider attestations. File hashes detect changes relative to the recorded files; they do not authenticate the origin of a model response.'
]),
('Related work', [
    'QuickCheck introduced property-based testing with generated inputs and custom generators [1]. Hypothesis provides this approach for Python, including example generation and shrinking [2]. ReproCase uses these existing mechanisms rather than proposing a new search or shrinking algorithm.',
    'CodaMOSA combines search-based test generation with language-model calls when coverage plateaus [3]. Our experiment instead constrains the model to a declarative assertion and tests it against known historical regressions. We did not compare with CodaMOSA or measure its coverage objectives.',
    'Deciding whether an output is correct is the established test-oracle problem [4]. The weak assertions observed here are an instance of that problem. The contribution is an inspectable comparison of contract acceptance, regression reproduction, and rejection of specific incorrect outputs within one small workflow.'
]),
('Limitations', [
    'The corpus contains only two preselected historical defects and two operation types. Human-written contracts, generators, and assertion operators substantially constrain the model task. The model may have encountered the public fixes during training. The immutable revision of the served weights is unknown, so repeating a request need not reproduce its response.',
    'Three responses per case do not support a representative estimate of model quality. Repeated boundary searches are deterministic and add no independent tasks. We did not perform a statistical superiority test, measure developer effort, test unfamiliar repositories, or compare models. The constructed output controls do not estimate a general false-acceptance rate. Different Python and Hypothesis versions also limit direct comparison between the two series.',
    'Gemma was not tested. Model inference was remote, while search and replay ran locally. The experiments therefore provide no evidence about a small local model or autonomous code repair.'
]),
('Conclusion', [
    'ReproCase reproduced the selected regressions in portable tests, but the model assertions offered no observed advantage over the manual controls. Input-domain choice affected detection, and two constructed outputs exposed checks that the model assertions omitted. A test can preserve a known regression while providing incomplete checks of returned values. Further evaluation should use new tasks and incorrect-output controls defined before inspecting model responses.'
]),
]

TABLES = {
    'live': ('Table 1. New responses and manual controls',
             ['Case', 'Requests', 'Confirmed\nmodel / manual', 'Calls per\ncondition'],
             [['CPython correlation', '3', '6/6 / 6/6', '2363'],
              ['Reversed range', '3', '6/6 / 6/6', '1548']], [2.25, .85, 2.05, 1.65]),
    'offline': ('Table 2. Separate local search series',
                ['Condition', 'Confirmed before fix', 'Findings after fix'],
                [['Model, wide domain', '12/12', '0/12'],
                 ['Model, narrow domain', '0/12', '0/12'],
                 ['Manual, wide domain', '12/12', '0/12'],
                 ['Manual, narrow domain', '0/12', '0/12'],
                 ['Manual, reversed range', '12/12', '0/12']], [3.2, 1.8, 1.8]),
    'controls': ('Table 3. Constructed incorrect outputs',
                 ['Input and incorrect output', 'Model assertion', 'Manual assertion'],
                 [['Correlation of two pairs [-1, 1]\nOutput 0 instead of 1', 'Accepts', 'Rejects'],
                  ['Reversed range from 0 to 5, step 1\nOutput [0, 1, 2, 3, 4]', 'Accepts', 'Rejects']], [3.7, 1.55, 1.55]),
}

REFERENCES = [
    ('1', 'Claessen K., Hughes J. QuickCheck: A lightweight tool for random testing of Haskell programs. ICFP, 2000, pp. 268-279.', 'https://research.chalmers.se/en/publication/237427'),
    ('2', 'MacIver D. R., Hatfield-Dodds Z., and contributors. Hypothesis: A new approach to property-based testing. JOSS, 2019, 4(43), 1891. DOI 10.21105/joss.01891.', 'https://joss.theoj.org/papers/10.21105/joss.01891'),
    ('3', 'Lemieux C., Inala J. P., Lahiri S. K., Sen S. CODAMOSA: Escaping Coverage Plateaus in Test Generation with Pre-trained Large Language Models. ICSE, 2023.', 'https://www.microsoft.com/en-us/research/publication/codamosa-escaping-coverage-plateaus-in-test-generation-with-pre-trained-large-language-models/'),
    ('4', 'Barr E. T., Harman M., McMinn P., Shahbaz M., Yoo S. The Oracle Problem in Software Testing: A Survey. IEEE TSE, 2015, 41(5), pp. 507-525. DOI 10.1109/TSE.2014.2372785.', 'https://discovery.ucl.ac.uk/id/eprint/1471263/'),
    ('5', 'WERFESTT. ReproCase. Original code and previously published evidence. Revision c8c7edc0a92a81183f1f8670871892cf9a4120f7.', 'https://github.com/WERFESTT/ReproCase/tree/c8c7edc0a92a81183f1f8670871892cf9a4120f7'),
    ('6', 'CPython. Correlation fix, pull request 107855. Source versions are pinned in the ReproCase manifest.', 'https://github.com/python/cpython/pull/107855'),
    ('7', 'more-itertools. Reversed numeric range fix, pull request 1153. Source versions are pinned in the ReproCase manifest.', 'https://github.com/more-itertools/more-itertools/pull/1153'),
]


def style_font(style, name='Liberation Serif', size=11):
    style.font.name = name
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor(0, 0, 0)
    props = style.element.get_or_add_rPr()
    for color in props.findall(qn('w:color')):
        for attr in ('themeColor', 'themeTint', 'themeShade'):
            color.attrib.pop(qn('w:' + attr), None)
    fonts = props.find(qn('w:rFonts'))
    if fonts is not None:
        for attr in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
            fonts.set(qn('w:' + attr), name)
    lang = props.find(qn('w:lang'))
    if lang is None:
        lang = OxmlElement('w:lang')
        props.append(lang)
    lang.set(qn('w:val'), 'en-US')


def hyperlink(paragraph, url):
    relation = paragraph.part.relate_to(url, 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink', is_external=True)
    link = OxmlElement('w:hyperlink')
    link.set(qn('r:id'), relation)
    run = OxmlElement('w:r')
    props = OxmlElement('w:rPr')
    color = OxmlElement('w:color')
    color.set(qn('w:val'), '000000')
    props.append(color)
    underline = OxmlElement('w:u')
    underline.set(qn('w:val'), 'single')
    props.append(underline)
    run.append(props)
    text = OxmlElement('w:t')
    text.text = 'Source'
    run.append(text)
    link.append(run)
    paragraph._p.append(link)


def add_table(doc, key):
    caption, headers, rows, widths = TABLES[key]
    p = doc.add_paragraph(caption)
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_before = Pt(7)
    p.runs[0].italic = True
    table = doc.add_table(rows=1, cols=len(headers))
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for column, width in zip(table.columns, widths):
        column.width = Inches(width)
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = value
    for values in rows:
        for cell, value in zip(table.add_row().cells, values):
            cell.text = value
    for row_index, row in enumerate(table.rows):
        props = row._tr.get_or_add_trPr()
        props.append(OxmlElement('w:cantSplit'))
        if row_index == 0:
            props.append(OxmlElement('w:tblHeader'))
        for column_index, cell in enumerate(row.cells):
            cell.width = Inches(widths[column_index])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell_props = cell._tc.get_or_add_tcPr()
            shading = OxmlElement('w:shd')
            shading.set(qn('w:fill'), 'E5EBF0' if row_index == 0 else 'FFFFFF')
            cell_props.append(shading)
            borders = OxmlElement('w:tcBorders')
            for side in ('top', 'bottom', 'left', 'right'):
                border = OxmlElement('w:' + side)
                for attr, value in (('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')):
                    border.set(qn('w:' + attr), value)
                borders.append(border)
            cell_props.append(borders)
            margins = OxmlElement('w:tcMar')
            for side, value in (('top', 85), ('bottom', 85), ('left', 105), ('right', 105)):
                margin = OxmlElement('w:' + side)
                margin.set(qn('w:w'), str(value))
                margin.set(qn('w:type'), 'dxa')
                margins.append(margin)
            cell_props.append(margins)
            for p in cell.paragraphs:
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT if column_index == 0 else WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                for run in p.runs:
                    run.bold = row_index == 0
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    doc = Document()
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.left_margin = section.right_margin = Inches(.85)
    section.top_margin = section.bottom_margin = Inches(.7)
    for style in doc.styles:
        for border in list(style.element.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    for name in ('Normal', 'Body Text', 'Title', 'Subtitle', 'Heading 1', 'Caption'):
        style_font(doc.styles[name])
    normal = doc.styles['Normal'].paragraph_format
    normal.line_spacing = 1.1
    normal.space_after = Pt(6)
    normal.widow_control = True
    for name, size in (('Title', 19), ('Subtitle', 12), ('Heading 1', 13)):
        style_font(doc.styles[name], 'Liberation Sans', size)
        props = doc.styles[name].paragraph_format
        props.keep_with_next = True
        props.space_after = Pt(6)
    doc.styles['Title'].paragraph_format.space_before = Pt(0)
    doc.styles['Heading 1'].paragraph_format.space_before = Pt(12)
    doc.add_paragraph(TITLE, style='Title')
    doc.add_paragraph(SUBTITLE, style='Subtitle')
    p = doc.add_paragraph('WERFESTT | 7 November 2026')
    p.paragraph_format.space_after = Pt(10)
    plain = [TITLE, SUBTITLE, 'WERFESTT | 7 November 2026', '']
    for heading, paragraphs in SECTIONS:
        doc.add_heading(heading, level=1)
        plain.extend([heading, ''])
        for paragraph in paragraphs:
            if isinstance(paragraph, dict):
                key = paragraph['table']
                add_table(doc, key)
                caption, headers, rows, _ = TABLES[key]
                plain.extend([caption, ' | '.join(headers)] + [' | '.join(row) for row in rows] + [''])
            else:
                doc.add_paragraph(paragraph)
                plain.extend([paragraph, ''])
    doc.add_heading('References', level=1)
    plain.extend(['References', ''])
    for number, text, url in REFERENCES:
        paragraph = doc.add_paragraph('[' + number + '] ' + text + ' ')
        paragraph.paragraph_format.keep_together = True
        hyperlink(paragraph, url)
        plain.extend(['[' + number + '] ' + text + ' ' + url, ''])
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    footer._p.append(field)
    props = doc.core_properties
    props.author = 'WERFESTT'
    props.title = TITLE
    props.subject = SUBTITLE
    props.language = 'en-US'
    props.comments = ''
    props.last_modified_by = 'WERFESTT'
    props.created = props.modified = datetime(2026, 11, 7, 18, 0, 0)
    text = '\n'.join(plain).rstrip() + '\n'
    if any(mark in text for mark in ('\u2014', '\u2013', '\u2015')):
        raise ValueError('В статье остались длинные тире')
    words = len(re.findall(r'\S+', text))
    if words > 3000:
        raise ValueError('Объём превышает 3000 слов')
    doc.save(args.out / 'ReproCase_article_en.docx')
    (args.out / 'ReproCase_article_en.txt').write_text(text, encoding='utf-8')
    print(json.dumps({'words': words, 'docx': str(args.out / 'ReproCase_article_en.docx')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
