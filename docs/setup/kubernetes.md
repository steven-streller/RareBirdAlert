# Kubernetes

Es gibt keine fertigen Manifeste im Repo – RareBirdAlert ist ein einzelner
Pod mit einer kleinen PVC für die SQLite-Datenbank, das lässt sich leicht in
ein bestehendes Manifest- oder GitOps-Setup (Flux, Argo CD, ...) einbauen.

Beispiel zum Anpassen (Namespace, Storage-Class, Image-Tag, Hostname etc. auf
die eigene Umgebung übertragen):

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: rarebirdalert
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: rarebirdalert-data
  namespace: rarebirdalert
spec:
  accessModes:
    - ReadWriteOnce
  # k3s liefert "local-path" standardmäßig mit; sonst longhorn, nfs-subdir-*, ...
  storageClassName: local-path
  resources:
    requests:
      storage: 512Mi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rarebirdalert
  namespace: rarebirdalert
spec:
  replicas: 1
  strategy:
    type: Recreate # SQLite auf einem ReadWriteOnce-Volume verträgt keine 2 Pods
  selector:
    matchLabels:
      app: rarebirdalert
  template:
    metadata:
      labels:
        app: rarebirdalert
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        # Macht die PVC für GID 1000 gruppenbeschreibbar - das Image bringt
        # selbst einen UID/GID-1000-User mit (appuser), besitzt aber nicht
        # automatisch, was der jeweilige Provisioner zurückgibt.
        fsGroup: 1000
      # Nur nötig, falls das ghcr.io/<owner>/rarebirdalert Package privat ist:
      #   kubectl create secret docker-registry ghcr-creds -n rarebirdalert \
      #     --docker-server=ghcr.io --docker-username=<gh-user> --docker-password=<gh-pat>
      # imagePullSecrets:
      #   - name: ghcr-creds
      containers:
        - name: rarebirdalert
          image: ghcr.io/steven-streller/rarebirdalert:latest
          ports:
            - containerPort: 8000
          env:
            - name: TZ
              value: Europe/Berlin
            - name: RAREBIRDALERT_DB_PATH
              value: /app/data/rarebirdalert.db
            - name: REGISTRATION_ENABLED
              value: "true"
            # Ohne dieses Secret generiert die App bei jedem Container-Start
            # einen zufälligen Schlüssel - das meldet nach jedem Neustart alle ab.
            # kubectl create secret generic rarebirdalert-secrets -n rarebirdalert \
            #   --from-literal=session-secret-key=$(python3 -c "import secrets; print(secrets.token_hex(32))")
            - name: SESSION_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: rarebirdalert-secrets
                  key: session-secret-key
                  optional: true
            # Optional, für ein höheres OpenSky-Rate-Limit - siehe die
            # Doku-Seite "Datenquelle (OpenSky)".
            # kubectl create secret generic rarebirdalert-opensky -n rarebirdalert \
            #   --from-literal=client-id=... --from-literal=client-secret=...
            - name: OPENSKY_CLIENT_ID
              valueFrom:
                secretKeyRef:
                  name: rarebirdalert-opensky
                  key: client-id
                  optional: true
            - name: OPENSKY_CLIENT_SECRET
              valueFrom:
                secretKeyRef:
                  name: rarebirdalert-opensky
                  key: client-secret
                  optional: true
          volumeMounts:
            - name: data
              mountPath: /app/data
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 300m
              memory: 384Mi
          # /readyz prüft die Datenbank-Erreichbarkeit - bei Fehlschlag nimmt
          # Kubernetes den Pod nur aus dem Traffic-Routing.
          readinessProbe:
            httpGet:
              path: /readyz
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          # /healthz prüft bewusst nichts außer "Prozess antwortet" - ein
          # Liveness-Fehlschlag killt und restartet den Pod, das würde eine
          # kurzzeitig nicht erreichbare Datenbank nur verschlimmern statt
          # beheben.
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 30
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: rarebirdalert-data
---
apiVersion: v1
kind: Service
metadata:
  name: rarebirdalert
  namespace: rarebirdalert
spec:
  selector:
    app: rarebirdalert
  ports:
    - port: 8000
      targetPort: 8000
```

Ressourcen-Bedarf ist gering: im Leerlauf ~100 MB RAM, kurze CPU-/RAM-Spitzen
beim wöchentlichen Import der Flugzeug-Metadatenbank (~500.000 Zeilen).

## Erreichbarkeit

Der `Service` oben ist `ClusterIP`. Für Zugriff von außen entweder

```bash
kubectl port-forward -n rarebirdalert svc/rarebirdalert 8000:8000
```

nutzen, oder eine `Ingress`-Ressource mit echtem Hostnamen ergänzen (k3s
bringt dafür Traefik mit):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: rarebirdalert
  namespace: rarebirdalert
spec:
  rules:
    - host: rarebirdalert.example.internal
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: rarebirdalert
                port:
                  number: 8000
```

## Non-root

Das Image läuft als `appuser` (UID/GID 1000). Der `securityContext` oben
(`runAsUser`/`runAsGroup`/`fsGroup: 1000`) ist nötig, damit die PVC für diese
UID beschreibbar gemountet wird – ohne passenden `fsGroup` bzw. ohne einen zur
UID passenden `/etc/passwd`-Eintrag im Image zeigt sich das als
"I have no name!" in einer interaktiven Shell.
