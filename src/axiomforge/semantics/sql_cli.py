from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from .pipeline_audit import AuditError
from .sql_compiler import compile_manifest_query,execute_readonly


def main(argv=None):
    p=argparse.ArgumentParser(prog='axiomforge-sql',description='Compile and optionally run read-only factorized diagnostic SQL')
    p.add_argument('--manifest',required=True)
    p.add_argument('--fact-table',required=True)
    p.add_argument('--dimension-tables',nargs='+',required=True)
    p.add_argument('--sql-out',required=True)
    p.add_argument('--sqlite-db',help='optional read-only local SQLite database')
    p.add_argument('--receipt-out')
    args=p.parse_args(argv)
    try:
        sql=compile_manifest_query(args.manifest,args.fact_table,args.dimension_tables)
        outfile=Path(args.sql_out);outfile.parent.mkdir(parents=True,exist_ok=True)
        outfile.write_text('-- Read-only AxiomForge factorized lineage diagnostic; inspect contract before use.\n'+sql+'\n',encoding='utf8')
        report={'status':'SQL_COMPILED','sql_file':str(outfile)}
        if args.sqlite_db:
            report['execution']=execute_readonly(args.sqlite_db,sql,preflight=(args.fact_table,args.dimension_tables,
                                                                     json.loads(Path(args.manifest).read_text(encoding='utf8'))['key'],
                                                                     json.loads(Path(args.manifest).read_text(encoding='utf8'))['measure'],
                                                                     [s['segment'] for s in json.loads(Path(args.manifest).read_text(encoding='utf8'))['stages']]))
        if args.receipt_out:
            target=Path(args.receipt_out);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    except (AuditError,ValueError,OSError) as exc:
        print(json.dumps({'status':'REJECTED','reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
