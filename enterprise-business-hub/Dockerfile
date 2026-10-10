# 企业业务中心镜像。多阶段构建：先用带 Maven 的镜像编译出 jar，
# 运行阶段只留 JRE + jar，不带构建工具，镜像小很多。
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
# MAVEN_MIRROR_URL：访问 Maven 中央仓库不稳时指定镜像站，例如 https://maven.aliyun.com/repository/public；默认不改。
ARG MAVEN_MIRROR_URL=
RUN if [ -n "$MAVEN_MIRROR_URL" ]; then \
        mkdir -p /root/.m2 && printf '<settings><mirrors><mirror><id>m</id><mirrorOf>central</mirrorOf><url>%s</url></mirror></mirrors></settings>' "$MAVEN_MIRROR_URL" > /root/.m2/settings.xml; \
    fi
# 直接编译实际项目，避免 dependency:go-offline 把与打包无关的 Maven 插件全部下载。
# BuildKit 缓存 Maven 仓库，后续修改源码后仍可复用已经下载的依赖。
COPY pom.xml .
COPY src ./src
RUN --mount=type=cache,target=/root/.m2/repository mvn -B -DskipTests package

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=build /build/target/*.jar app.jar
EXPOSE 8090
ENTRYPOINT ["java", "-jar", "app.jar"]
