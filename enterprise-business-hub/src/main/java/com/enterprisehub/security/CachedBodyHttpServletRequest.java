package com.enterprisehub.security;

import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import org.springframework.util.StreamUtils;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/**
 * 把请求体整个读进内存缓存下来，{@link #getInputStream()}/{@link #getReader()}
 * 每次调用都返回一份基于缓存的新流——{@link SignedRequestContextFilter} 要在
 * Controller 的 {@code @RequestBody} 反序列化之前先读一遍body去算 SHA-256 核对
 * 签名，原生 {@code HttpServletRequest} 的输入流只能读一次，读完之后 Controller
 * 那边就拿不到数据了；这是 Spring 生态处理"过滤器要读 body，下游还要再读一遍"
 * 这个场景的标准写法（{@code org.springframework.web.util.ContentCachingRequestWrapper}
 * 解决的是不同的问题——它按需缓存但不保证下游能从缓存里重新读，这里自己写一个
 * 更直接：构造时就把 body 全部读进 byte[]，之后谁调 getInputStream() 都是从这份
 * 内存拷贝里读，不会撞到"流已经被消费过"的问题）。
 */
public class CachedBodyHttpServletRequest extends HttpServletRequestWrapper {
    private final byte[] cachedBody;

    public CachedBodyHttpServletRequest(HttpServletRequest request) throws IOException {
        super(request);
        this.cachedBody = StreamUtils.copyToByteArray(request.getInputStream());
    }

    public byte[] getCachedBody() {
        return cachedBody;
    }

    @Override
    public ServletInputStream getInputStream() {
        return new CachedBodyServletInputStream(cachedBody);
    }

    @Override
    public BufferedReader getReader() {
        return new BufferedReader(new InputStreamReader(new ByteArrayInputStream(cachedBody), StandardCharsets.UTF_8));
    }

    private static final class CachedBodyServletInputStream extends ServletInputStream {
        private final ByteArrayInputStream buffer;

        private CachedBodyServletInputStream(byte[] cachedBody) {
            this.buffer = new ByteArrayInputStream(cachedBody);
        }

        @Override
        public boolean isFinished() {
            return buffer.available() == 0;
        }

        @Override
        public boolean isReady() {
            return true;
        }

        @Override
        public void setReadListener(ReadListener readListener) {
            throw new UnsupportedOperationException("异步 IO 不需要，这个请求体缓存包装器不支持");
        }

        @Override
        public int read() {
            return buffer.read();
        }

        @Override
        public int read(byte[] b, int off, int len) {
            return buffer.read(b, off, len);
        }
    }
}
