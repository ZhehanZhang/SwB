import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const data = volume("switchboard-data", { sizeMB: 1024 });
  const switchboard = service("switchboard", {
    source: github("YOUR-ORG/YOUR-REPO"),
    build: { builder: "DOCKERFILE" },
    deploy: { requiredMountPath: "/data" },
    healthcheck: "/healthz",
    replicas: 1,
    env: {
      SWITCHBOARD_DATA: "/data",
      SWITCHBOARD_PUBLIC_URL: preserve(),
      SWITCHBOARD_KEY_OWNER: preserve(),
    },
    volumeMounts: { "/data": data },
  });

  return project("switchboard", { resources: [switchboard, data] });
});
