#!/usr/bin/env python3
"""Verify the actual local-only debug APK, not just Gradle's success message.
Requires Android SDK build-tools (ANDROID_HOME) and Java 17 on PATH.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ASSETS=['app.js','access-client.js','premium-contact.json','sw.js','styles.css','index.html','library-data.js',
        'mission-data.js','mission-data-extras.js','manifest.webmanifest','icon.svg',
        'icon-192.png','icon-512.png','images/aki.webp','images/bimo.webp','images/naya.webp',
        'images/rina.webp','images/suki.webp']
MODEL_SHA='30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498'


def verify(apk, previous=None):
    sdk=Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or '/nonexistent')
    versions=sorted((sdk/'build-tools').glob('*'),key=lambda p:tuple(int(x) for x in p.name.split('.') if x.isdigit()))
    if not versions:raise RuntimeError('Set ANDROID_HOME ke SDK dengan build-tools.')
    bt=versions[-1]
    def tool(name,*args):
        return subprocess.check_output([str(bt/name),*map(str,args)],text=True,stderr=subprocess.STDOUT)
    signature=tool('apksigner','verify','--verbose','--print-certs',apk)
    badging=tool('aapt','dump','badging',apk)
    manifest=tool('aapt2','dump','xmltree','--file','AndroidManifest.xml',apk)
    network=tool('aapt2','dump','xmltree','--file','res/xml/eq_network_security_config_debug.xml',apk)
    package=re.search(r"package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'",badging)
    assert package and package.groups()==('com.englishquest.app.debug','13','1.7.1-debug'),'Identitas/version APK tidak sesuai'
    assert re.search(r'debuggable.*(?:true|0xffffffff)',manifest),'Bukan manifest debug'
    assert 'cleartextTrafficPermitted=false' in network,'Kebijakan default cleartext tidak ditolak'
    hosts=re.findall(r"T: '([^']+)'",network)
    assert sorted(hosts)==['127.0.0.1','localhost'],'Pengecualian cleartext bukan loopback-only'
    with zipfile.ZipFile(apk) as archive:
        assert archive.testzip() is None,'Arsip APK rusak'
        for name in ASSETS:
            payload=archive.read('assets/'+name)
            assert payload==(ROOT/name).read_bytes(),'Aset APK berbeda dari root: '+name
            assert payload==(ROOT/'english-quest-android/app/src/main/assets'/name).read_bytes(),'Aset Android stale: '+name
        app=archive.read('assets/app.js').decode()
        contact=json.loads(archive.read('assets/premium-contact.json'))
        assert contact['dana_number']=='0856-5670-6104' and contact['whatsapp_number']=='0856-5670-6104'
        assert contact['recipient_name']=='' and contact['price_idr']==5000
        assert contact['whatsapp_message']==json.loads((ROOT/'premium-contact.json').read_text())['whatsapp_message']

        for marker in ('renderManualPurchaseCard','submitManualPayment','KONFIRMASI PEMBAYARAN VIA WHATSAPP','Rp5.000'):
            assert marker in app,'Alur manual tidak dibundel: '+marker
        assert b'/manual-payments/config' in archive.read('assets/access-client.js')
        dex=b''.join(archive.read(n) for n in archive.namelist() if n.endswith('.dex'))
        for marker in (b'getApiBaseUrl',b'isHttpDevAllowed',b'http://127.0.0.1:8088',b'wa.me',b'copyDanaNumber',b'getPremiumContactConfig'):
            assert marker in dex,'Bridge/origin debug hilang dari DEX'
        assert hashlib.sha256(archive.read('assets/vosk-model-small-en-us-0.15.zip')).hexdigest()==MODEL_SHA
        forbidden=[n for n in archive.namelist() if n.endswith(('.sqlite3','.db','.jks','.keystore','.pem','.key')) or '/.env' in n]
        assert not forbidden,'Berkas DB/secret tidak boleh ikut APK'
        secret_pattern=rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|EQP-[A-Fa-f0-9]{48}|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----)'
        for payload in [dex]+[archive.read('assets/'+n) for n in ASSETS if n.endswith(('.js','.html'))]:
            assert re.search(secret_pattern,payload) is None,'Pola kredensial/kode plaintext tidak diharapkan'
    cert_pattern=r'Signer #1 certificate SHA-256 digest: (\w+)'
    cert=re.search(cert_pattern,signature)[1]
    report={'status':'PASS','apk':str(apk.resolve()),'bytes':apk.stat().st_size,
        'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'applicationId':package[1],
        'versionCode':int(package[2]),'versionName':package[3],'assets_identical':len(ASSETS),
        'api_origin':'http://127.0.0.1:8088','loopback_only':True,'vosk_sha256':MODEL_SHA,
        'signer_sha256':cert,'secrets_pattern_scan':'no match; not an exhaustive secrets audit',
        'scope':'local debug only; not device-tested or online-ready'}
    if previous:
        old=tool('apksigner','verify','--print-certs',previous)
        report['previous_apk']=str(previous)
        report['same_signer_as_previous']=re.search(cert_pattern,old)[1]==cert
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk',type=Path)
    parser.add_argument('--previous-apk',type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(args.apk,args.previous_apk),indent=2))
