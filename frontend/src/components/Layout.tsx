import { Link, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Package,
  DollarSign,
  TrendingUp,
  Truck,
  Mail,
} from 'lucide-react'

const nav = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/materials', icon: Package, label: 'Materials' },
  { to: '/costs', icon: DollarSign, label: 'Costs' },
  { to: '/forecasting', icon: TrendingUp, label: 'Forecasting' },
  { to: '/suppliers', icon: Truck, label: 'Suppliers' },
  { to: '/rfq', icon: Mail, label: 'RFQ' },
]

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  return (
    <div className="flex h-screen">
      <aside className="w-64 bg-slate-900 text-white flex flex-col">
        <div className="p-6 text-xl font-bold tracking-tight">ICI Platform</div>
        <nav className="flex-1 px-4 space-y-2">
          {nav.map((item) => {
            const Icon = item.icon
            const active = location.pathname === item.to
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`flex items-center gap-3 px-4 py-3 rounded-lg transition ${
                  active
                    ? 'bg-slate-700 text-white'
                    : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                }`}
              >
                <Icon size={18} />
                <span className="font-medium">{item.label}</span>
              </Link>
            )
          })}
        </nav>
        <div className="p-4 text-xs text-slate-500">
          Industrial Cost Intelligence v1.0.0
        </div>
      </aside>
      <main className="flex-1 overflow-auto p-8">{children}</main>
    </div>
  )
}
