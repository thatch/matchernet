# -*- coding: utf-8 -*-
import numpy as np


class Regularization:
    def __init__(self):
        self.lambd         = 0.1
        self.d_lambda      = 1.0
        self.lambda_factor = 1.6
        self.lambda_max    = 1e10
        self.lambda_min    = 1e-6
    
    def on_diverge(self):
        self.d_lambda = max(self.d_lambda * self.lambda_factor, self.lambda_factor)
        self.lambd = max(self.lambd * self.d_lambda, self.lambda_min)
        self.lambd = min(self.lambd, self.lambda_max)

    def get_current_lamba(self):
        return self.lambd

    

class iLQG(object):
    def __init__(self, dynamics, cost):
        self.dynamics = dynamics
        self.stored_cost = cost

    def optimize(self,
                 x_init,
                 u_init,
                 T,
                 start_time_step=0,
                 iter_max=20,
                 stop_criteria=1e-3):

        assert x_init.shape == (self.dynamics.x_dim,)
        assert u_init.shape == (T, self.dynamics.u_dim)
        
        self.start_time_step = start_time_step
        
        # Clone cost
        cost = self.stored_cost.clone()
        
        x_list = self.init_trajectory(x_init, u_init, T, cost)
        
        u_list = np.copy(u_init)

        self.regularization = Regularization()
        
        for i in range(iter_max):
            print("iter={}".format(i))

            for j in range(10):
                lambd = self.regularization.get_current_lamba()
                diverged, k_list, K_list = self.backward(x_list, u_list, T, cost, lambd)
                if not diverged:
                    break
                else:
                    self.regularization.on_diverge()
                    print("update lambda={}".format(
                        self.regularization.get_current_lamba()))

            # Clone cost
            cost = self.stored_cost.clone()
            
            x_list, u_list, diff = self.forward(x_list, u_list, x_init,
                                                k_list, K_list, T, cost)

            if(diff < stop_criteria):
                print("it={}, diff={}".format(i, diff))
                break
            
        return np.array(x_list, dtype=np.float32), \
            np.array(u_list, dtype=np.float32), \
            np.array(K_list, dtype=np.float32)
    
    def init_trajectory(self, x_init, u_init, T, cost):
        x_list = [x_init]
        
        for i in range(T):
            cost.apply_state(x_list[i], (self.start_time_step + i) * self.dynamics.dt)
            next_x = self.dynamics.value(x_list[i], u_init[i])
            x_list.append(next_x)
            
        return x_list

    def forward(self, x_list, u_list, x_init, k_list, K_list, T, cost):
        next_x_list = [x_init]
        next_u_list = []
        
        diff = 0.0
        alpha = 1.0
        
        for t in range(T):
            cost.apply_state(next_x_list[t], (self.start_time_step + t) * self.dynamics.dt)
            
            next_u = u_list[t] + alpha * k_list[t] + \
                     K_list[t] @ (next_x_list[t] - x_list[t])
            next_u_list.append(next_u)
            next_x = self.dynamics.value(next_x_list[t], next_u_list[t])
            next_x_list.append(next_x)
            
            diff += np.sum(np.abs(u_list[t] - next_u_list[t]))

        return next_x_list, next_u_list, diff

    def backward(self, x_list, u_list, T, cost, lambd):
        k_list = []
        K_list = []

        # Derivatives of the terminal cost
        lx  = cost.x( x_list[T], None, self.start_time_step * self.dynamics.dt)
        lxx = cost.xx(x_list[T], None, self.start_time_step * self.dynamics.dt)
        
        Vx  = lx  # (x_dim,)
        Vxx = lxx # (x_dim,x_dim)
        
        assert Vx.shape  == (self.dynamics.x_dim,)
        assert Vxx.shape == (self.dynamics.x_dim, self.dynamics.x_dim)

        diverged = False
        
        for t in range(T-1, -1, -1):
            x = x_list[t]
            u = u_list[t]
            
            assert x.shape == (self.dynamics.x_dim,)
            assert u.shape == (self.dynamics.u_dim,)
            
            # Derivatives of the dynamics
            fx = self.dynamics.x(x, u)
            fu = self.dynamics.u(x, u)

            # Derivatives of the running cost
            lx  = cost.x( x, u, (self.start_time_step+t) * self.dynamics.dt)
            lxx = cost.xx(x, u, (self.start_time_step+t) * self.dynamics.dt)
            lu  = cost.u( x, u, (self.start_time_step+t) * self.dynamics.dt)
            luu = cost.uu(x, u, (self.start_time_step+t) * self.dynamics.dt)
            lux = cost.ux(x, u, (self.start_time_step+t) * self.dynamics.dt)

            # Derivatives of the Q function
            Qx  = lx  + fx.T @ Vx
            Qu  = lu  + fu.T @ Vx
            Qxx = lxx + fx.T @ Vxx @ fx
            Quu = luu + fu.T @ Vxx @ fu
            Qux = lux + fu.T @ Vxx @ fx

            # Regularlize
            Quu = Quu + np.eye(self.dynamics.u_dim) + lambd
            
            assert Qx.shape  == (self.dynamics.x_dim,)
            assert Qu.shape  == (self.dynamics.u_dim,)
            assert Qxx.shape == (self.dynamics.x_dim, self.dynamics.x_dim)
            assert Quu.shape == (self.dynamics.u_dim, self.dynamics.u_dim)
            assert Qux.shape == (self.dynamics.u_dim, self.dynamics.x_dim)
            
            diverged, Quu_inv = self.calc_inverse(Quu)
            if diverged:
                print("Diverged")
                break
            
            k = -Quu_inv @ Qu
            K = -Quu_inv @ Qux
            
            assert k.shape == (self.dynamics.u_dim,)
            assert K.shape == (self.dynamics.u_dim, self.dynamics.x_dim)

            Vx  = Qx  + K.T @ Quu @ k + K.T @ Qu  + Qux.T @ k
            Vxx = Qxx + K.T @ Quu @ K + K.T @ Qux + Qux.T @ K
            
            assert Vx.shape  == (self.dynamics.x_dim,)
            assert Vxx.shape == (self.dynamics.x_dim, self.dynamics.x_dim)
            
            k_list.append(k)
            K_list.append(K)
            
        k_list.reverse()
        K_list.reverse()

        return diverged, k_list, K_list

    def calc_inverse(self, m):
        try:
            R = np.linalg.cholesky(m)
        except np.linalg.LinAlgError:
            return True, None
        R_inv = np.linalg.inv(R)
        m_inv = R_inv.T @ R_inv
        return False, m_inv
