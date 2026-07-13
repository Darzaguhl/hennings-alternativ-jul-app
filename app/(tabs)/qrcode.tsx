import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';
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
          setError('Log in to view your QR code.');
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
          setError('No QR code available for this account yet.');
        }
      } catch (err) {
        console.error('Failed to fetch QR code', err);
        if (active) {
          setError('We could not load your QR code. Pull to refresh or try again later.');
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
      <Text style={styles.title}>Your QR Code</Text>
      {loading && <ActivityIndicator size="large" color="#333" />}
      {!loading && qrValue && <QRCode value={qrValue} size={200} />}
      {!loading && !qrValue && <Text style={styles.error}>{error ?? 'No QR code available.'}</Text>}
      {!loading && qrValue && (
        <Text style={styles.info}>
          Show this QR code to the event master to check in.
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 20 },
  title: { fontSize: 24, fontWeight: 'bold', marginBottom: 20 },
  info: { marginTop: 20, fontSize: 16, textAlign: 'center' },
  error: { marginTop: 20, fontSize: 16, textAlign: 'center', color: 'crimson' },
});
