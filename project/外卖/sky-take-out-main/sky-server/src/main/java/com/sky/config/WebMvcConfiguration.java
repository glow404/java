package com.sky.config;

import com.sky.interceptor.JwtTokenAdminInterceptor;
import com.sky.interceptor.JwtTokenUserInterceptor;
import com.sky.json.JacksonObjectMapper;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Spring MVC 配置类。
 *
 * <p>这个类不负责处理某一个具体的业务请求，而是集中配置 Web 层的通用规则，
 * 例如：请求进入 Controller 之前是否需要校验 JWT，以及 Java 对象如何转换成 JSON。
 *
 * <p>{@code @Configuration} 表示这是一个 Spring 配置类。启动时，Spring 会通过组件扫描
 * 找到它，并将它注册为 Spring 容器中的 Bean；随后 Spring MVC 会调用
 * {@link WebMvcConfigurer} 提供的扩展方法，应用这里定义的 MVC 配置。
 *
 * <p>{@code @Slf4j} 是 Lombok 注解，会自动生成一个名为 {@code log} 的日志对象。
 * 它属于 Lombok 的代码生成能力，不是 Spring MVC 注解。
 */
@Configuration
@Slf4j
public class WebMvcConfiguration implements WebMvcConfigurer {

    /**
     * 管理端 JWT 拦截器。
     *
     * <p>拦截器对象由 Spring 容器创建和管理，{@code @Autowired} 表示启动时将容器中的
     * {@link JwtTokenAdminInterceptor} Bean 注入到这里。管理端和用户端使用不同拦截器，
     * 是因为两类请求使用的登录身份、JWT 载荷和校验逻辑可能不同。
     */
    @Autowired
    private JwtTokenAdminInterceptor jwtTokenAdminInterceptor;

    /**
     * 用户端 JWT 拦截器。
     *
     * <p>该拦截器专门负责校验用户端请求携带的登录令牌，并将登录用户信息保存到当前请求
     * 可以访问的上下文中，供后续 Controller 或 Service 使用。
     */
    @Autowired
    private JwtTokenUserInterceptor jwtTokenUserInterceptor;

    /**
     * 注册 Spring MVC 拦截器。
     *
     * <p>请求到达 Controller 之前，Spring MVC 会按照注册规则执行拦截器。这里通过
     * {@link InterceptorRegistry} 将不同拦截器绑定到不同 URL 范围：
     *
     * <ul>
     *     <li>{@code /admin/**}：所有管理端接口；</li>
     *     <li>{@code /user/**}：所有用户端接口。</li>
     * </ul>
     *
     * <p>{@code excludePathPatterns} 用于声明公开接口。登录接口必须放行，否则用户在没有
     * JWT 的情况下无法访问登录接口；店铺营业状态接口也被项目设计为无需登录即可访问。
     *
     * <p>典型请求顺序可以理解为：
     * <pre>
     * HTTP 请求
     *     -> DispatcherServlet
     *     -> 匹配并执行拦截器
     *     -> Controller
     *     -> Service
     * </pre>
     * 如果 JWT 校验失败，拦截器通常会直接返回错误响应，请求不会继续进入 Controller。
     *
     * @param registry Spring MVC 提供的拦截器注册表
     */
    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        // 管理端请求统一经过管理端 JWT 拦截器。
        registry.addInterceptor(jwtTokenAdminInterceptor)
                // /admin/** 中的 ** 表示匹配 /admin/ 下的所有子路径。
                .addPathPatterns("/admin/**")
                // 登录接口必须放行，否则未登录用户无法取得 JWT。
                .excludePathPatterns("/admin/employee/login");

        // 用户端请求统一经过用户端 JWT 拦截器。
        registry.addInterceptor(jwtTokenUserInterceptor)
                // 匹配所有以 /user/ 开头的用户端接口。
                .addPathPatterns("/user/**")
                // 用户登录接口不要求携带 JWT。
                .excludePathPatterns("/user/user/login")
                // 查询店铺营业状态的接口被设计为公开接口，不要求登录。
                .excludePathPatterns("/user/shop/status");
    }

    /**
     * 扩展 Spring MVC 默认的 HTTP 消息转换器。
     *
     * <p>{@link HttpMessageConverter} 负责 HTTP 请求/响应消息和 Java 对象之间的转换：
     *
     * <ul>
     *     <li>请求方向：将 JSON 请求体反序列化为 Controller 参数对象；</li>
     *     <li>响应方向：将 Controller 返回的 Java 对象序列化为 JSON 响应体。</li>
     * </ul>
     *
     * <p>{@link MappingJackson2HttpMessageConverter} 是 Spring MVC 中基于 Jackson 的
     * JSON 转换器。项目通过 {@link JacksonObjectMapper} 定制 Jackson 行为，例如日期、
     * 时间的序列化和反序列化格式，以及未知 JSON 字段的处理方式。
     *
     * <p>把转换器插入索引 {@code 0}，表示将它放到转换器列表的最前面，使 Spring MVC
     * 优先尝试使用项目自定义的 Jackson 配置。
     *
     * @param converters Spring MVC 当前维护的 HTTP 消息转换器列表
     */
    @Override
    public void extendMessageConverters(List<HttpMessageConverter<?>> converters) {
        // 记录自定义 JSON 转换器已经开始注册，便于启动日志排查配置是否生效。
        log.info("Registering custom Jackson message converter");

        // 创建一个基于 Jackson 的 JSON 消息转换器。
        MappingJackson2HttpMessageConverter converter = new MappingJackson2HttpMessageConverter();

        // 使用项目自定义的 ObjectMapper，统一控制 Java 对象与 JSON 的转换规则。
        converter.setObjectMapper(new JacksonObjectMapper());

        // 放到列表首位，使自定义转换器拥有更高的匹配优先级。
        converters.add(0, converter);
    }
}
