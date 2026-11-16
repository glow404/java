

## 二、怎么用（三步）

### 第 1 步：引入依赖

### 第 2 步：写配置类（核心）

`com/sky/config/WebSocketConfiguration.java`：

```java
package com.sky.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.server.standard.ServerEndpointExporter;

@Configuration
public class WebSocketConfiguration {

    @Bean
    public ServerEndpointExporter serverEndpointExporter() {
        return new ServerEndpointExporter();
    }
}
```

这个配置类WebSocketConfiguration的语义可以概括成一句：**替内嵌 Tomcat 把 `@ServerEndpoint` 的注册动作补一遍。**

### 第 3 步：写端点类

`com/sky/websocket/WebSocketServer.java`：

```java
package com.sky.websocket;

import com.sky.constant.WebSocketMessageConstant;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import javax.websocket.*;
import javax.websocket.server.PathParam;
import javax.websocket.server.ServerEndpoint;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

@Component
@ServerEndpoint("/ws/{sid}")
@Slf4j
public class WebSocketServer {

    /**
     * 保存所有在线客户端：sid -> Session
     * 必须是 static，因为每个连接都是容器 new 出来的不同实例
     */
    private static Map<String, Session> sessionMap = new HashMap<>();

    /**
     * 静态注入 RedisTemplate，供容器 new 出来的实例使用
     */
    private static RedisTemplate redisTemplate;

    @Autowired
    public void setRedisTemplate(RedisTemplate redisTemplate) {
        WebSocketServer.redisTemplate = redisTemplate;
    }

    /**
     * 连接建立
     */
    @OnOpen
    public void onOpen(Session session, @PathParam("sid") String sid) {
        log.info("客户端连接，sid：{}", sid);
        sessionMap.put(sid, session);
    }

    /**
     * 连接关闭
     */
    @OnClose
    public void onClose(@PathParam("sid") String sid) {
        log.info("客户端断开，sid：{}", sid);
        sessionMap.remove(sid);
    }

    /**
     * 收到客户端消息（心跳等）
     */
    @OnMessage
    public void onMessage(String message, @PathParam("sid") String sid) {
        log.info("收到客户端消息，sid：{}，内容：{}", sid, message);
        // 心跳回显，保持连接活跃
        try {
            Session session = sessionMap.get(sid);
            if (session != null) {
                session.getBasicRemote().sendText("pong");
            }
        } catch (IOException e) {
            log.error("发送心跳失败", e);
        }
    }

    /**
     * 群发给所有在线客户端
     */
    public void sendToAllClient(String message) {
        for (Session session : sessionMap.values()) {
            try {
                session.getBasicRemote().sendText(message);
            } catch (IOException e) {
                log.error("推送消息失败", e);
            }
        }
    }
}
```

**这里有个必须注意的点：** `@ServerEndpoint` 标注的类，实例是**容器在每次建连时 new 出来的**，不是 Spring 管理的 Bean。所以类里直接 `@Autowired private RedisTemplate redisTemplate;` 会注入成 null。上面的**静态 setter 注入**就是绕开这个问题的常规做法：`@Component` 让 Spring 管理一份实例，Spring 创建它时把 `RedisTemplate` 写进静态字段，容器后续 new 出的实例通过静态字段共享。`sessionMap` 同理必须 `static`，否则存不住全局连接。

------

## 三、全流程代码串一遍

### 环节 1：启动时注册端点

```
SkyApplication 启动
  → 内嵌 Tomcat 就绪，ServletContext 中放入 ServerContainer
  → Spring 扫描到 WebSocketConfiguration
  → 创建 ServerEndpointExporter Bean
  → afterPropertiesSet() 执行：
       取 ServerContainer → 扫描 @ServerEndpoint → addEndpoint(WebSocketServer.class)
  → 端点 /ws/{sid} 在容器中生效
```

### 环节 2：前端建立连接

管理端（Vue）在页面加载时连上：

```js
// 管理端：建立 WebSocket 连接
const ws = new WebSocket("ws://192.168.x.x:8080/ws/1")

ws.onopen = () => {
  console.log("连接建立")
  // 启动心跳，防止空闲被断开
  setInterval(() => ws.send("ping"), 30000)
}

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data)
  if (msg.type === "ORDER_MESSAGE") {
    // 来单提醒：弹提示音 + 刷新订单列表
    playOrderSound()
    refreshOrderList()
  } else if (msg.type === "REMIND_MESSAGE") {
    // 客户催单提醒
    showRemind(msg)
  }
}

ws.onclose = () => {
  console.log("连接关闭，可在此做重连")
}
```

小程序端（用户催单）用 `uni.connectSocket`：

```js
const socket = uni.connectSocket({ url: "wss://域名/ws/2" })

socket.onOpen(() => console.log("已连接"))
socket.onMessage((res) => {
  // 处理服务端推送
})
```

连接成功后，服务端 `onOpen` 被触发，`sessionMap` 里就多了一条 `sid -> Session` 记录。

### 环节 3：业务触发推送

以"用户下单 → 管理端来单提醒"为例。

`user/OrderController` 下单接口调用 Service：

```java
@PostMapping
@ApiOperation("下单")
public Result submit(@RequestBody OrderSubmitDTO orderSubmitDTO) {
    orderService.submitOrder(orderSubmitDTO);
    return Result.success();
}
```

`OrderServiceImpl` 在订单落库后，通过 Redis 发布消息（解耦业务与推送）：

```java
@Override
public void submitOrder(OrderSubmitDTO orderSubmitDTO) {
    // 1. 校验、计算金额、写入 order / order_detail 表
    ...
    // 2. 发布来单提醒
    Map<String, Object> map = new HashMap<>();
    map.put("type", WebSocketMessageConstant.ORDER_MESSAGE);
    map.put("content", "您有新的订单，请及时处理");
    redisTemplate.convertAndSend("order", map);
}
```

Redis 消息监听器收到后调用 `WebSocketServer` 群发：

```java
@Component
@Slf4j
public class RedisMessageListener implements MessageListener {

    @Autowired
    private WebSocketServer webSocketServer;

    @Override
    public void onMessage(Message message, byte[] pattern) {
        try {
            String json = new String(message.getBody());
            log.info("收到 redis 消息：{}", json);
            // 群发给所有在线管理端
            webSocketServer.sendToAllClient(json);
        } catch (Exception e) {
            log.error("处理消息失败", e);
        }
    }
}
```

客户催单同理，`OrderServiceImpl` 里的催单方法发一条 `REMIND_MESSAGE` 到同一个 Redis 频道，最终也走 `sendToAllClient`。

### 环节 4：消息到达客户端

```
sendToAllClient(json)
  → 遍历 sessionMap
  → session.getBasicRemote().sendText(json)
  → 前端 ws.onmessage 收到
  → 解析 type，播放提示音 / 刷新列表
```

### 环节 5：断开清理

浏览器关闭、页面刷新、网络中断 → 容器触发 `@OnClose` → `sessionMap.remove(sid)`，避免向已失效的 Session 写数据抛异常。











## 

------

## 二、基础设施三件套（两个案例共用）

### 1. Maven 坐标

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-websocket</artifactId>
</dependency>
```

### 2. `config/WebSocketConfiguration.java`——注册端点

```java
@Configuration
public class WebSocketConfiguration {
    @Bean
    public ServerEndpointExporter serverEndpointExporter() {
        return new ServerEndpointExporter();
    }
}
```

Spring Boot fat jar + 内嵌 Tomcat 时，容器不会自动扫描 `@ServerEndpoint`，这个 Bean 负责把端点注册进容器的 `ServerContainer`，否则前端握手时找不到端点、连接失败。

### 3. `websocket/WebSocketServer.java`——连接管理与推送出口

```java
@Component
@ServerEndpoint("/ws/{sid}")
public class WebSocketServer {

    // 存放会话对象
    private static Map<String, Session> sessionMap = new HashMap<>();

    @OnOpen
    public void onOpen(Session session, @PathParam("sid") String sid) {
        System.out.println("客户端：" + sid + " 建立连接");
        sessionMap.put(sid, session);
    }

    @OnMessage
    public void onMessage(String message, @PathParam("sid") String sid) {
        System.out.println("收到来自客户端：" + sid + " 的信息：" + message);
    }

    @OnClose
    public void onClose(@PathParam("sid") String sid) {
        System.out.println("连接断开：" + sid);
        sessionMap.remove(sid);
    }

    // 群发
    public void sendToAllClient(String message) {
        Collection<Session> sessions = sessionMap.values();
        for (Session session : sessions) {
            try {
                session.getBasicRemote().sendText(message);
            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }
}
```

------

## 三、案例一：来单提醒（支付成功后推送）

### 触发位置：`OrderServiceImpl.paySuccess`

官方版是在支付成功后（更新订单状态之后）追加推送逻辑：先按订单号查出订单，更新状态为"待接单"、支付状态为"已支付"、写入结账时间，然后构造消息并广播。

```java
@Service
@Slf4j
public class OrderServiceImpl implements OrderService {

    @Autowired
    private OrderMapper orderMapper;

    @Autowired
    private WebSocketServer webSocketServer;   // 注入 WebSocket 服务端组件

    public void paySuccess(String outTradeNo) {
        // 1. 查询订单
        Orders ordersDB = orderMapper.getByNumber(outTradeNo);

        // 2. 更新订单状态、支付状态、结账时间
        Orders orders = Orders.builder()
                .id(ordersDB.getId())
                .status(Orders.TO_BE_CONFIRMED)
                .payStatus(Orders.PAID)
                .checkoutTime(LocalDateTime.now())
                .build();
        orderMapper.update(orders);

        // 3. 构造消息并推送
        Map map = new HashMap();
        map.put("type", 1);                      // 1 表示来单提醒
        map.put("orderId", ordersDB.getId());
        map.put("content", "订单号：" + outTradeNo);
        String json = JSON.toJSONString(map);
        webSocketServer.sendToAllClient(json);   // 向管理端浏览器推送
    }
}
```

------

## 四、案例二：客户催单（HTTP 请求触发推送）

### 1. Controller：`user/OrderController`

```java
/**
 * 用户催单
 */
@GetMapping("/reminder/{id}")
@ApiOperation("用户催单")
public Result reminder(@PathVariable("id") Long id) {
    orderService.reminder(id);
    return Result.success();
}
```

接口路径 `GET /user/order/reminder/{id}`，只提交订单 id 这一个路径参数。

### 3. Service 实现：`OrderServiceImpl.reminder`

```java
public void reminder(Long id) {
    // 查询订单是否存在
    Orders orders = orderMapper.getById(id);
    if (orders == null) {
        throw new OrderBusinessException(MessageConstant.ORDER_NOT_FOUND);
    }

    // 基于 WebSocket 实现催单
    Map map = new HashMap();
    map.put("type", 2);                      // 2 代表客户催单
    map.put("orderId", id);
    map.put("content", "订单号：" + orders.getNumber());
    webSocketServer.sendToAllClient(JSON.toJSONString(map));
}
```

------

## 五、前端（管理端）怎么消费这两个消息

管理端登录后建立长连接，收到消息按 `type` 分流，做弹窗和语音播报：

```js
// 登录成功后建立连接
const ws = new WebSocket(`ws://localhost:8080/ws/${userId}`)

ws.onopen = () => console.log('已连接')

ws.onmessage = (event) => {
  const data = JSON.parse(event.data)
  if (data.type === 1) {
    // 来单提醒：弹窗 + 语音
    showNotification('新订单', data.content)
    playSound('/audio/new_order.mp3')
  } else if (data.type === 2) {
    // 催单提醒
    showNotification('客户催单', data.content)
    playSound('/audio/remind.mp3')
  }
}

ws.onclose = () => {
  console.log('连接断开，可在此重连')
}
```



