import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';
import { colors, fonts, theme } from '../../constants/theme';
import { useAuth } from '../AuthContext';

export default function QRCodeScreen() {
  const { apiFetch, currentUser } = useAuth();
  const [qrValue, setQrValue] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const loadQr = async () => {
      if (!currentUser) {
        if (active) {
          setQrValue(null);
          setError('Logg inn for å se din QR-kode.');
          setLoading(false);
        }
        return;
      }

      try {
        const response = await apiFetch('/api/qrcodes/');
        if (!response.ok) {
          throw new Error('Failed to load QR code');
        }

        const payload = await response.json();
        const record = Array.isArray(payload) ? payload[0] : payload;

        if (record?.data) {
          if (active) {
            setQrValue(record.data as string);
            setError(null);
          }
        } else if (active) {
          setError('Ingen QR-kode tilgjengelig for denne kontoen ennå.');
        }
      } catch (err) {
        console.error('Failed to fetch QR code', err);
        if (active) {
          setError('Kunne ikke laste QR-koden. Dra for å oppdatere eller prøv igjen senere.');
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    loadQr();

    return () => {
      active = false;
    };
  }, [apiFetch, currentUser]);

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Din QR-kode</Text>
      {loading && <ActivityIndicator size="large" color={theme.primary} />}
      {!loading && qrValue && (
        <View style={styles.qrWrapper}>
          <QRCode value={qrValue} size={200} color={theme.primaryDark} />
        </View>
      )}
      {!loading && !qrValue && <Text style={styles.error}>{error ?? 'Ingen QR-kode tilgjengelig.'}</Text>}
      {!loading && qrValue && (
        <Text style={styles.info}>
          Vis denne QR-koden til en innsjekk-ansvarlig for å sjekke inn.
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 20, backgroundColor: theme.background },
  title: { fontSize: 24, fontFamily: fonts.displayBold, marginBottom: 20, color: theme.primaryDark },
  qrWrapper: { padding: 20, backgroundColor: colors.white, borderRadius: 16 },
  info: { marginTop: 20, fontSize: 16, textAlign: 'center', fontFamily: fonts.body, color: theme.textMuted },
  error: { marginTop: 20, fontSize: 16, textAlign: 'center', fontFamily: fonts.body, color: theme.danger },
});
