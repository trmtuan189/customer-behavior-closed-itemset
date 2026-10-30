"""Auditable aggregation; missing facts stay null, never inferred from baskets.

Source baskets follow the existing cancellation/positive Quantity/positive Price/
explicit StockCode exclusions. Missing identifiers and nonfinite numbers are
additionally rejected and counted. No source or benchmark file is rewritten.
Cross-worksheet invoice collisions are NEVER merged for metadata linkage.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import math
import statistics
import unicodedata

BAD_CODES=frozenset({'B','ADJUST','ADJUST2','AMAZONFEE','BANK CHARGES','C2','DOT',
                     'POST','D','23444','M','m'})
COLUMNS=('Invoice','StockCode','Description','Quantity','InvoiceDate','Price','Customer ID','Country')


def missing(value):
    return value is None or (isinstance(value,float) and math.isnan(value)) or (
        isinstance(value,str) and not value.strip())


def identifier(value):
    if missing(value) or isinstance(value,bool):
        return None
    return str(value).strip()


def customer_id(value):
    if missing(value) or isinstance(value,bool):
        return None
    if isinstance(value,(int,float,Decimal)):
        num=number(value)
        return str(int(num)) if num is not None and num>0 and num==num.to_integral_value() else None
    text=str(value).strip()
    if text.upper() in {'NAN','NONE','N/A','NA','<NA>','NULL','UNKNOWN'}:
        return None
    return text


def number(value):
    if missing(value) or isinstance(value,bool):
        return None
    try:
        d=Decimal(str(value))
        return d if d.is_finite() else None
    except InvalidOperation:
        return None


def timestamp(value):
    if isinstance(value,datetime):
        return value if value.tzinfo is None else None
    if isinstance(value,date):
        return datetime.combine(value,datetime.min.time())
    if isinstance(value,str):
        try:
            d=datetime.fromisoformat(value.strip())
            return d if d.tzinfo is None else None
        except ValueError:
            pass
    return None


def description(value):
    if missing(value):
        return None
    text=' '.join(unicodedata.normalize('NFKC',str(value)).split()).upper()
    placeholders={'UNKNOWN','N/A','NA','NONE','NAN','<NA>','MISSING','?','??'}
    return text if any(c.isalpha() for c in text) and text not in placeholders else None


def descriptive(values):
    if not values:
        return {'n':0,'min':None,'max':None,'mean':None,'median':None,'p95':None}
    ordered=sorted(values)
    pos=(len(ordered)-1)*.95
    lo=int(pos)
    p95=ordered[lo]+(ordered[min(lo+1,len(ordered)-1)]-ordered[lo])*(pos-lo)
    return {'n':len(values),'min':ordered[0],'max':ordered[-1],
            'mean':statistics.mean(values),'median':statistics.median(values),'p95':p95}


def basket_analysis(baskets):
    products=Counter()
    sizes=[]
    for basket in baskets:
        items=set(basket)
        sizes.append(len(items))
        products.update(items)
    n=len(sizes)
    return {'n_transactions':n,'n_products':len(products),'basket_size':descriptive(sizes),
            'total_distinct_item_occurrences':sum(sizes),
            'singleton_baskets':sizes.count(1),'empty_baskets':sizes.count(0)},products,Counter(sizes)


def audit_source(records, *, max_rows=1200000, memory_guard=None):
    """Stream rows, collect worksheet-qualified invoices and name evidence.

    Exact duplicate rows are reported, retained for original basket compatibility,
    and counted once in a sensitivity amount. Monetary eligibility is stricter:
    invoices with duplicates are excluded from monetary/RFM, never silently
    declared to be verified revenue. Both source and deduplicated totals remain.
    """
    sheets={}
    groups={}
    votes=defaultdict(lambda:defaultdict(set))
    fingerprints=defaultdict(set)
    n_total=0
    for record in records:
        n_total+=1
        if n_total>max_rows:
            raise ValueError('Source row budget exceeded; output is incomplete')
        if memory_guard and n_total%5000==0:
            memory_guard()
        sheet=record['_sheet']
        if sheet not in sheets:
            sheets[sheet]={'rows':0,'blank_rows':0,'valid_rows':0,'missing':Counter(),
                'types':{c:Counter() for c in COLUMNS},'flags':Counter(),
                'exact_duplicate_rows':0,'date_min':None,'date_max':None}
        audit=sheets[sheet]
        audit['rows']+=1
        if all(missing(record.get(c)) for c in COLUMNS):
            audit['blank_rows']+=1
        for c in COLUMNS:
            v=record.get(c)
            audit['missing'][c]+=missing(v)
            audit['types'][c][type(v).__name__]+=1
        values=tuple(repr(record.get(c)) for c in COLUMNS)
        digest=hashlib.sha256(repr(values).encode('utf-8')).digest()
        duplicate=digest in fingerprints[sheet]
        fingerprints[sheet].add(digest)
        audit['exact_duplicate_rows']+=duplicate
        invoice=identifier(record.get('Invoice'))
        code=identifier(record.get('StockCode'))
        qty,price=number(record.get('Quantity')),number(record.get('Price'))
        dt=timestamp(record.get('InvoiceDate'))
        cid=customer_id(record.get('Customer ID'))
        flags={
            'cancelled_invoice':bool(invoice and invoice.upper().startswith('C')),
            'missing_invoice':invoice is None,'missing_stockcode':code is None,
            'special_stockcode':code in BAD_CODES,
            'quantity_invalid':qty is None,'quantity_negative':qty is not None and qty<0,
            'quantity_zero':qty==0,'price_invalid':price is None,
            'price_nonpositive':price is not None and price<=0,
            'missing_customer_id':cid is None,'invalid_or_missing_date':dt is None,
        }
        audit['flags'].update(k for k,v in flags.items() if v)
        if dt is not None:
            iso=dt.isoformat()
            audit['date_min']=min(audit['date_min'],iso) if audit['date_min'] else iso
            audit['date_max']=max(audit['date_max'],iso) if audit['date_max'] else iso
        valid=(invoice is not None and code is not None and not flags['cancelled_invoice']
               and code not in BAD_CODES and qty is not None and qty>0 and price is not None and price>0)
        if not valid:
            continue
        audit['valid_rows']+=1
        key=(sheet,invoice)
        if key not in groups:
            groups[key]={'sheet':sheet,'invoice':invoice,'items':set(),'dates':set(),
                'customer_ids':set(),'countries':set(),'source_line_count':0,
                'duplicate_lines':0,'quantity_source':Decimal(0),'amount_source':Decimal(0),
                'quantity_unique':Decimal(0),'amount_unique':Decimal(0),
                'missing_date_lines':0,'missing_customer_lines':0,'missing_country_lines':0}
        g=groups[key]
        g['items'].add(code)
        g['source_line_count']+=1
        g['duplicate_lines']+=duplicate
        g['quantity_source']+=qty
        g['amount_source']+=qty*price
        if not duplicate:
            g['quantity_unique']+=qty
            g['amount_unique']+=qty*price
        if dt is not None: g['dates'].add(dt.isoformat())
        else: g['missing_date_lines']+=1
        if cid is not None: g['customer_ids'].add(cid)
        else: g['missing_customer_lines']+=1
        country=identifier(record.get('Country'))
        if country: g['countries'].add(country)
        else: g['missing_country_lines']+=1
        name=description(record.get('Description'))
        if name:
            votes[code][name].add(key)
    quality={'source_rows':n_total,'valid_rows':sum(s['valid_rows'] for s in sheets.values()),
             'worksheets':sheets,'n_invoice_keys':len(groups),
             'missing_customer_ids_do_not_form_one_customer':True,
             'duplicate_policy':'Source lines retained for basket compatibility; ambiguous duplicate amounts excluded from monetary/RFM'}
    return groups,votes,quality


def product_catalog(products,votes=None, *, source_available=False,min_votes=2):
    if min_votes<1:
        raise ValueError('min_votes must be positive')
    catalog=[]
    for code,count in sorted(products.items()):
        candidates=(votes or {}).get(code,{})
        ranked=sorted(((name,len(keys)) for name,keys in candidates.items()),key=lambda v:(-v[1],v[0]))
        name,winning=(ranked[0] if ranked else (None,0))
        total=sum(v for _,v in ranked)
        tied=len(ranked)>1 and ranked[1][1]==winning
        reliable=bool(name and winning>=min_votes and not tied and 2*winning>=total)
        status=('missing_source' if not source_available else 'no_valid_description' if not ranked
                else 'ambiguous_tie' if tied else 'insufficient_votes' if winning<min_votes
                else 'weak_dominance' if 2*winning<total else 'verified_source_name')
        catalog.append({'stock_code':code,'description':name if reliable else None,
            'representative_candidate':name,'reliable_name':reliable,'name_status':status,
            'n_descriptions':len(ranked),'n_name_votes':total,'winning_votes':winning,
            'candidate_names':[{'description':k,'invoice_votes':v} for k,v in ranked],
            'invoice_count':count})
    return catalog


def link_transactions(invoice_baskets,groups,verified_copy_proofs=None):
    by_invoice=defaultdict(list)
    for (sheet,invoice),g in groups.items():
        by_invoice[invoice].append(g)
    linked=[]
    resolved=0
    for invoice,items in invoice_baskets.items():
        candidates=by_invoice.get(invoice,[])
        base={'invoice':invoice,'distinct_products':len(set(items)),'items':sorted(items)}
        if not candidates:
            linked.append({**base,'link_status':'source_invoice_not_found'})
            continue
        source_keys=sorted((g['sheet'],g['invoice']) for g in candidates)
        if len(candidates)>1:
            proof=(verified_copy_proofs or {}).get(invoice,{})
            same_fields=('items','dates','customer_ids','countries','source_line_count','duplicate_lines',
                         'quantity_source','quantity_unique','amount_source','amount_unique',
                         'missing_date_lines','missing_customer_lines','missing_country_lines')
            agreed=(proof.get('identical_multisets') is True and
                    sorted(tuple(k) for k in proof.get('source_keys',[]))==source_keys and
                    all(all(g[f]==candidates[0][f] for f in same_fields) for g in candidates[1:]))
            if not agreed:
                linked.append({**base,'link_status':'ambiguous_cross_worksheet','candidate_keys':len(candidates)})
                continue
            resolved+=1
        # Choosing one proven copy is not concatenating or summing source rows.
        g=min(candidates,key=lambda r:r['sheet'])
        if set(items)!=g['items']:
            linked.append({**base,'link_status':'basket_mismatch'})
            continue
        dt=next(iter(g['dates'])) if len(g['dates'])==1 and g['missing_date_lines']==0 else None
        cid=next(iter(g['customer_ids'])) if len(g['customer_ids'])==1 and g['missing_customer_lines']==0 else None
        country=next(iter(g['countries'])) if len(g['countries'])==1 and g['missing_country_lines']==0 else None
        eligible=bool(dt and g['duplicate_lines']==0)
        linked.append({**base,'link_status':'verified','sheet':g['sheet'],'invoice_date':dt,
            'equivalent_source_keys':source_keys,
            'linkage_basis':'identical_raw_multiset_copies' if len(candidates)>1 else 'unique_source_key',
            'customer_id':cid,'country':country,'source_line_count':g['source_line_count'],
            'duplicate_lines':g['duplicate_lines'],'quantity_source':str(g['quantity_source']),
            'quantity_deduplicated_sensitivity':str(g['quantity_unique']),
            'amount_source':str(g['amount_source']),
            'amount_deduplicated_sensitivity':str(g['amount_unique']),
            'monetary_eligible':eligible,'order_value':str(g['amount_source']) if eligible else None,
            'metadata_conflicts':{'date':dt is None,'customer':cid is None,'country':country is None}})
    counts=Counter(r['link_status'] for r in linked)
    return linked,{'n_transactions':len(linked),'verified_linked':counts['verified'],
                   'verified_coverage':counts['verified']/len(linked) if linked else None,
                   'status_counts':dict(counts),'cross_sheet_keys_never_merged':True,
                   'resolved_identical_cross_sheet_invoices':resolved,
                   'physical_rows_not_summed_across_sheets':True}


def collapse_copy_name_votes(votes,linked):
    """Proven duplicate sheet copies are one invoice, not two name observations."""
    aliases={}
    for row in linked:
        keys=[tuple(k) for k in row.get('equivalent_source_keys',[])]
        if row.get('link_status')=='verified' and len(keys)>1:
            for key in keys: aliases[key]=min(keys)
    removed=0
    for names in votes.values():
        for name,keys in names.items():
            collapsed={aliases.get(k,k) for k in keys}
            removed+=len(keys)-len(collapsed)
            names[name]=collapsed
    return removed


def pseudonym(value,key):
    return 'cust_'+hmac.new(key,value.encode('utf-8'),hashlib.sha256).hexdigest()


def customer_behavior(linked):
    people={}
    months={}
    for r in linked:
        if r.get('link_status')!='verified': continue
        cid=r.get('customer_id')
        if cid:
            c=people.setdefault(cid,{'customer_id':cid,'frequency':0,'products':set(),
                'known_order_value':Decimal(0),'orders_missing_value':0})
            c['frequency']+=1
            c['products'].update(r['items'])
            if r.get('order_value') is not None: c['known_order_value']+=Decimal(r['order_value'])
            else: c['orders_missing_value']+=1
        if r.get('invoice_date'):
            month=r['invoice_date'][:7]
            m=months.setdefault(month,{'month':month,'invoices':0,'customers':set(),
                'gross_value_eligible':Decimal(0),'invoices_with_value':0})
            m['invoices']+=1
            if cid: m['customers'].add(cid)
            if r.get('order_value') is not None:
                m['gross_value_eligible']+=Decimal(r['order_value'])
                m['invoices_with_value']+=1
    private=[{**r,'products':sorted(r['products']),'distinct_products':len(r['products']),
              'known_order_value':str(r['known_order_value'])} for r in people.values()]
    public_months=[{'month':m['month'],'invoices':m['invoices'],'known_active_customers':len(m['customers']),
        'gross_value_eligible':str(m['gross_value_eligible']),
        'invoices_with_value':m['invoices_with_value']} for _,m in sorted(months.items())]
    frequency=Counter(r['frequency'] for r in private)
    return private,public_months,frequency


def midrank_scores(values,reverse=False):
    counts=Counter(values)
    less=0
    scores={}
    n=len(values)
    for value in sorted(counts):
        score=min(5,1+(5*(2*less+counts[value]))//(2*n))
        scores[value]=6-score if reverse else score
        less+=counts[value]
    return [scores[v] for v in values]


def rfm(linked):
    dates=[datetime.fromisoformat(r['invoice_date']) for r in linked
           if r.get('link_status')=='verified' and r.get('invoice_date')]
    reference=max(dates).date()+timedelta(days=1) if dates else None
    people={}
    seen=set()
    for r in linked:
        if r.get('link_status')!='verified' or not r.get('customer_id') or not r.get('monetary_eligible'):
            continue
        key=(r['sheet'],r['invoice'])
        if key in seen: raise ValueError('Duplicate invoice supplied to RFM')
        seen.add(key)
        cid=r['customer_id']
        p=people.setdefault(cid,{'customer_id':cid,'last_purchase':None,'frequency':0,'monetary':Decimal(0)})
        day=datetime.fromisoformat(r['invoice_date']).date()
        p['last_purchase']=max(day,p['last_purchase']) if p['last_purchase'] else day
        p['frequency']+=1
        p['monetary']+=Decimal(r['order_value'])
    rows=[]
    for cid,p in sorted(people.items()):
        rows.append({'customer_id':cid,'recency_days':(reference-p['last_purchase']).days,
                     'frequency':p['frequency'],'monetary':p['monetary']})
    if rows:
        for field,reverse,label in [('recency_days',True,'r_score'),('frequency',False,'f_score'),('monetary',False,'m_score')]:
            for row,score in zip(rows,midrank_scores([r[field] for r in rows],reverse)):
                row[label]=score
        for r in rows:
            a,b,c=r['r_score'],r['f_score'],r['m_score']
            r['segment']=('Điểm RFM cao' if min(a,b,c)>=4 else 'Tần suất cao, lâu chưa mua' if a<=2 and b>=4
                          else 'Gần đây, tần suất thấp' if a>=4 and b<=2
                          else 'Lâu chưa mua, tần suất thấp' if a<=2 and b<=2 else 'Nhóm trung gian')
            r['monetary']=str(r['monetary'])
    return rows,{'reference_date':reference.isoformat() if reference else None,
        'n_customers':len(rows),'eligible_invoices':len(seen),
        'segments':dict(Counter(r['segment'] for r in rows)),
        'score_method':'1+floor(5*(less+0.5*ties)/N), clamped to 5; R reversed; ties never split'}


def enrich_rule(rule,catalog,source_hash):
    result=dict(rule)
    for side in ['antecedents','consequents']:
        result[side+'_products']=[{'stock_code':code,
            'description':catalog.get(code,{}).get('description'),
            'name_status':catalog.get(code,{}).get('name_status','not_in_catalog')}
            for code in rule[side]]
    result['enrichment']={'schema_version':'1.0','source_rules_sha256':source_hash,
                         'source_rule_id':rule['rule_id']}
    return result


def verify_public(records):
    """Public structured outputs may not contain customer-level identifiers."""
    banned={'customer_id','customer id','customer_key','customer_token','raw_customer_id'}
    def walk(value):
        if isinstance(value,dict):
            if any(str(k).lower() in banned for k in value):
                raise ValueError('Customer-level identifier in public output')
            for v in value.values(): walk(v)
        elif isinstance(value,list):
            for v in value: walk(v)
    walk(records)
    return True
