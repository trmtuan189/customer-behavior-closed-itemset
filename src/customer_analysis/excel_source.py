"""Read-only Excel source; separate XML row count for independent validation."""
from pathlib import PurePosixPath
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .core import COLUMNS

SHEETS=('Year 2009-2010','Year 2010-2011')


def read_excel(path):
    import openpyxl
    workbook=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        if set(workbook.sheetnames)!=set(SHEETS):
            raise ValueError(f'Expected exactly {SHEETS}, found {workbook.sheetnames}')
        for name in SHEETS:
            sheet=workbook[name]
            iterator=sheet.iter_rows(values_only=True)
            header=next(iterator,None)
            if header is None:
                raise ValueError(f'Missing header in {name}')
            columns=[str(c).strip() if c is not None else '' for c in header]
            if len(set(columns))!=len(columns) or not set(COLUMNS)<=set(columns):
                raise ValueError(f'Invalid or duplicate columns in {name}')
            for index,row in enumerate(iterator,2):
                yield {'_sheet':name,'_row':index,**dict(zip(columns,row))}
    finally:
        workbook.close()


def xml_row_counts(path):
    """Count nonempty physical rows without using the Excel reader/aggregator.

    Header is physical row 1. Counts also expose blank formatted rows and gaps;
    compare nonempty source rows rather than assuming max_row equals real rows.
    """
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    out={}
    with ZipFile(path) as archive:
        rels=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets={r.attrib['Id']:r.attrib['Target'] for r in rels}
        workbook=ET.fromstring(archive.read('xl/workbook.xml'))
        for sheet in workbook.find('m:sheets',ns):
            target=targets[sheet.attrib['{'+ns['r']+'}id']]
            member=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            count=nonempty=0
            with archive.open(member) as file:
                for event,element in ET.iterparse(file,events=('end',)):
                    if element.tag.endswith('}row'):
                        if int(element.attrib.get('r',1))!=1:
                            count+=1
                            nonempty+=any(c.find('m:v',ns) is not None or c.find('m:is',ns) is not None
                                          for c in element)
                        element.clear()
            out[sheet.attrib['name']]={'physical_data_rows':count,'nonempty_data_rows':nonempty}
    return out
