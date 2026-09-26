"""Sellado y verificacion OpenTimestamps desde el servidor Flask."""
import hashlib
import os
from datetime import datetime, timezone, timedelta

OTS_CALENDARS = [
    'https://alice.btc.calendar.opentimestamps.org',
    'https://bob.btc.calendar.opentimestamps.org',
    'https://finney.calendar.eternitywall.com',
]


def now_local_iso():
    return (datetime.now(timezone.utc) + timedelta(hours=-3)).strftime('%Y-%m-%d %H:%M:%S') + ' (UTC-3)'


def write_receipt(ots_path, filepath, digest_hex, submitted, errors, status,
                  stamped_at=None, verified_at=None, bitcoin_blocks=None, extra_lines=None):
    receipt_path = ots_path + '.txt'
    consult = stamped_at or now_local_iso()
    if verified_at:
        validacion = verified_at
    elif status == 'confirmed':
        validacion = now_local_iso()
    else:
        validacion = 'Pendiente (el sello de tiempo aun no esta confirmado de forma publica)'

    receipt_lines = [
        'OpenTimestamps — recibo de prueba de existencia',
        '',
        f'Archivo: {os.path.basename(filepath)}',
        f'Prueba: {os.path.basename(ots_path)}',
        f'SHA256: {digest_hex}',
        f'Estado: {"sello de tiempo confirmado" if status == "confirmed" else "pendiente de confirmacion publica"}',
        '',
        f'Fecha de consulta (sello enviado a calendarios): {consult}',
        f'Fecha de validacion: {validacion}',
        '',
        'Calendarios que aceptaron el sello:',
    ]
    receipt_lines.extend(f'  - {url}' for url in (submitted or []))
    if bitcoin_blocks:
        receipt_lines.append('Referencias de confirmacion publica:')
        receipt_lines.extend(f'  - {height}' for height in bitcoin_blocks)
    if errors:
        receipt_lines.append('Calendarios con error:')
        receipt_lines.extend(f'  - {err}' for err in errors)
    if extra_lines:
        receipt_lines.append('')
        receipt_lines.extend(extra_lines)
    receipt_lines.append('')
    receipt_lines.append('El archivo .ots es la prueba criptografica. Guardalo junto a la captura.')
    with open(receipt_path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(receipt_lines) + '\n')
    return receipt_path


def sha256_file(filepath):
    digest = hashlib.sha256()
    with open(filepath, 'rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()


def stamp_file(filepath, ots_path=None, stamped_at=None):
    """Crea un .ots (prueba incompleta hasta la confirmacion publica del sello)."""
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.serialize import StreamSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

    if not os.path.exists(filepath):
        raise FileNotFoundError(filepath)

    digest = sha256_file(filepath)
    detached = DetachedTimestampFile(OpSHA256(), Timestamp(digest))
    submitted = []
    errors = []

    for calendar_url in OTS_CALENDARS:
        try:
            calendar = RemoteCalendar(calendar_url)
            calendar_ts = calendar.submit(digest, timeout=8)
            detached.timestamp.merge(calendar_ts)
            submitted.append(calendar_url)
        except Exception as exc:
            errors.append(f'{calendar_url}: {exc}')

    if not submitted:
        raise RuntimeError('Ningun calendario OpenTimestamps acepto el sello. ' + '; '.join(errors))

    if not ots_path:
        ots_path = filepath + '.ots'

    with open(ots_path, 'wb') as handle:
        ctx = StreamSerializationContext(handle)
        detached.serialize(ctx)

    stamped_at = stamped_at or now_local_iso()
    receipt_path = write_receipt(
        ots_path,
        filepath,
        digest.hex(),
        submitted,
        errors,
        'pending',
        stamped_at=stamped_at,
        verified_at=None
    )

    return {
        'ots_path': ots_path,
        'receipt_path': receipt_path,
        'sha256': digest.hex(),
        'calendars': submitted,
        'errors': errors,
        'status': 'pending',
        'stamped_at': stamped_at
    }


def load_detached(ots_path):
    from opentimestamps.core.serialize import StreamDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    with open(ots_path, 'rb') as handle:
        ctx = StreamDeserializationContext(handle)
        return DetachedTimestampFile.deserialize(ctx)


def verify_file(filepath, ots_path):
    """Comprueba que el .ots corresponde al archivo y resume el estado del sello."""
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation

    if not os.path.exists(filepath):
        return {'ok': False, 'status': 'missing_file', 'message': 'No esta el archivo de la captura'}
    if not os.path.exists(ots_path):
        return {'ok': False, 'status': 'missing_ots', 'message': 'No hay prueba .ots'}

    detached = load_detached(ots_path)
    actual = sha256_file(filepath)
    if actual != detached.file_digest:
        return {
            'ok': False,
            'status': 'mismatch',
            'message': 'El SHA256 del archivo no coincide con la prueba OpenTimestamps',
            'sha256_file': actual.hex(),
            'sha256_ots': detached.file_digest.hex()
        }

    pending = []
    bitcoin = []

    def walk(timestamp):
        for attestation in timestamp.attestations:
            if isinstance(attestation, PendingAttestation):
                pending.append(str(getattr(attestation, 'uri', attestation)))
            elif isinstance(attestation, BitcoinBlockHeaderAttestation):
                bitcoin.append(getattr(attestation, 'height', None))
        for result_ts in timestamp.ops.values():
            walk(result_ts)

    walk(detached.timestamp)

    if bitcoin:
        return {
            'ok': True,
            'status': 'confirmed',
            'message': 'Sello de tiempo confirmado (referencia %s)' % (bitcoin[0],),
            'sha256': actual.hex(),
            'bitcoin_blocks': bitcoin,
            'pending_calendars': pending
        }

    return {
        'ok': True,
        'status': 'pending',
        'message': 'Sello enviado a calendarios publicos. Pendiente de confirmacion.',
        'sha256': actual.hex(),
        'pending_calendars': pending
    }


def upgrade_proof(ots_path):
    """Pide a los calendarios el camino de confirmacion, si ya esta listo."""
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.notary import PendingAttestation
    from opentimestamps.core.serialize import StreamSerializationContext

    detached = load_detached(ots_path)
    upgraded = 0

    def walk(timestamp):
        nonlocal upgraded
        for attestation in list(timestamp.attestations):
            if isinstance(attestation, PendingAttestation):
                uri = str(getattr(attestation, 'uri', '')).rstrip('/')
                commitment = timestamp.msg
                try:
                    calendar = RemoteCalendar(uri)
                    updated = calendar.get_timestamp(commitment, timeout=8)
                    timestamp.merge(updated)
                    upgraded += 1
                except Exception:
                    pass
        for result_ts in list(timestamp.ops.values()):
            walk(result_ts)

    walk(detached.timestamp)

    with open(ots_path, 'wb') as handle:
        ctx = StreamSerializationContext(handle)
        detached.serialize(ctx)

    return {'upgraded': upgraded, 'ots_path': ots_path}
