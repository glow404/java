/** 应用入口：配置中文组件与统一主题，挂载账号隔离的数据工作台。 */
import ReactDOM from 'react-dom/client';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#167e80', borderRadius: 10, fontFamily: 'Inter, "Microsoft YaHei", sans-serif', colorBgLayout: '#f4f6f8' } }}>
    <AntApp><App /></AntApp>
  </ConfigProvider>,
);
