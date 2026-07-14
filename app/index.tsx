import { useRouter } from "expo-router";
import { useEffect } from "react";
import { Image, StyleSheet, Text, View } from "react-native";
import { colors, fonts } from "../constants/theme";
import { useAuth } from "./AuthContext";

export default function SplashScreen() {
  const router = useRouter();
  const { loading, token } = useAuth();

  useEffect(() => {
    if (loading) return;

    const timer = setTimeout(() => {
      if (token) {
        router.replace("/events");
      } else {
        router.replace("login");
      }
    }, 800);

    return () => clearTimeout(timer);
  }, [loading, token, router]);

  return (
    <View style={styles.container}>
      <Image source={require("../assets/images/icon.png")} style={styles.logo} />
      <Text style={styles.text}>Hennings Alternativ Jul</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: colors.green700 },
  logo: { width: 160, height: 160, marginBottom: 20, borderRadius: 80 },
  text: { fontSize: 22, color: colors.cream50, fontFamily: fonts.displayBold },
});
