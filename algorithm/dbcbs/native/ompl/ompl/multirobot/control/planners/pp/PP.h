/*********************************************************************
* Software License Agreement (BSD License)
*
*  Copyright (c) 2008, Willow Garage, Inc.
*  All rights reserved.
*
*  Redistribution and use in source and binary forms, with or without
*  modification, are permitted provided that the following conditions
*  are met:
*
*   * Redistributions of source code must retain the above copyright
*     notice, this list of conditions and the following disclaimer.
*   * Redistributions in binary form must reproduce the above
*     copyright notice, this list of conditions and the following
*     disclaimer in the documentation and/or other materials provided
*     with the distribution.
*   * Neither the name of the Willow Garage nor the names of its
*     contributors may be used to endorse or promote products derived
*     from this software without specific prior written permission.
*
*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
*  POSSIBILITY OF SUCH DAMAGE.
*********************************************************************/

/* Author: Justin Kottinger */

#ifndef OMPL_MULTIROBOT_CONTROL_PLANNERS_PP_
#define OMPL_MULTIROBOT_CONTROL_PLANNERS_PP_

#include "ompl/multirobot/control/planners/PlannerIncludes.h"
#include "ompl/base/Planner.h"


namespace ompl
{
    namespace multirobot
    {
        namespace control
        {
            /**
            @anchor cPP
            @par Short description
            Prioritized Planning (PP) is a decoupled planning framework 
            where robots are assigned a priority and planned for sequentially. 
            Robots of lower priority must avoid higher priority robots by 
            treating them as dynamic obstacles.
            */

            /** \brief PP Algorithm */
            class PP : public multirobot::base::Planner
            {
            public:
                /** \brief Constructor */
                PP(const SpaceInformationPtr &si, ompl::base::PlannerPtr solver = nullptr);

                /** \brief Destructor */
                ~PP() override;

                void addPathAsDynamicObstacles(const unsigned int index, const ompl::control::PathControlPtr &path);

                void getPlannerData(ompl::base::PlannerData &data) const override;

                ompl::base::PlannerStatus solve(const ompl::base::PlannerTerminationCondition &ptc) override;

                void clear() override;

                void setup() override;

            protected:

                /** \brief Free the memory allocated by this planner */
                void freeMemory();

                /** \brief An ordered container containing a solver for every individual */
                std::vector<ompl::base::PlannerPtr> llSolvers_;

                /** \brief The base::SpaceInformation cast as control::SpaceInformation, for convenience */
                const SpaceInformation *siC_;
            };
        }
    }
}

#endif
